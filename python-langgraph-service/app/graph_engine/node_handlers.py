import asyncio
import inspect
import json
import logging
import re
from dataclasses import replace
from typing import Dict, Set, Callable, Awaitable, Any, List, Optional, Tuple

import grpc
from langchain_core.runnables import RunnableConfig
from langgraph.errors import GraphInterrupt
from langgraph.pregel import Pregel

from app.clients.llm import LLMCredentialSet, LLMCredentials, MissingUserApiKeyError, \
    missing_user_key_message
from app.config import settings
from app.generated import langgraph_pb2_grpc, langgraph_pb2
from app.graph_engine.document_parser import (
    DocumentLimits,
    DocumentParseError,
    parse_document,
    read_sheets,
)
from app.graph_engine.table_normalize import NormalizeOptions
from app.graph_engine.graph_utils import format_state_context, extract_json_from_response, JsonGenerationError, \
    parse_literal, GraphState, DOCUMENT_REF_KEY, document_variables_of

logger = logging.getLogger(__name__)

GraphLoader = Callable[[str], Awaitable[Pregel]]

# --- Per-user credentials travel through RunnableConfig.configurable ---------------
# The backend (Spring) puts these keys on the run; they are popped out of the gRPC
# request context by the servicer BEFORE the context becomes GraphState, so a credential
# can never be checkpointed, echoed back to the browser, or embedded in a prompt
# (state["context"] is appended to the prompt by _process_action).
#
# Note there is deliberately NO backend-declared "who will pay" flag: billing is DERIVED
# per node from the credentials that were actually resolved (see llm_billing), because one
# run can legitimately mix a personal key and the shared one.
CONFIG_LLM_CREDENTIALS = "llm_credentials_json"   # JSON: provider -> {api_key, folder_id}
CONFIG_REQUIRE_USER_KEY = "require_user_key"       # "1" = fail instead of using the service key
CONFIG_USER_ID = "x_user_id"                       # attribution (per-run metering/logs)
CONFIG_CALLER_KIND = "caller_kind"                 # "b2b" = word the refusal for an integrator
# Comma-separated context keys this graph's DOCUMENT nodes read, resolved once per run by the
# servicer. It rides in configurable - never in GraphState - so an ACTION node can hide those
# keys from the prompt: a custom-named reference is as useless to a model as `document_ref`,
# and showing it is what makes a model announce it cannot open a file that has already been
# extracted into a variable. Not a secret, purely an opaque handle.
CONFIG_DOCUMENT_REF_KEYS = "document_ref_keys"

# Keys that must be inherited by subgraphs (subgraph nodes run with the parent's user).
INHERITED_CONFIGURABLE_KEYS = (
    CONFIG_LLM_CREDENTIALS, CONFIG_REQUIRE_USER_KEY, CONFIG_USER_ID, CONFIG_CALLER_KIND,
    CONFIG_DOCUMENT_REF_KEYS,
)

# Context keys that would leak a secret if left in GraphState.context. Popped by the
# servicer, and scrubbed again before that state is rendered into a prompt - belt and
# braces, because a client can always send its own context map.
SECRET_CONTEXT_KEYS = frozenset({"llm_api_key", "yc_folder_id", "llm_credentials_json"})

# Everything in GraphState.context that has no business being in a prompt: the credentials
# above (a leak to the model's provider), and the document reference - an opaque handle a
# model can neither read nor act on, which will only be quoted back in an answer about not
# being able to open the file. The handle STAYS in the state: a DOCUMENT node reads it from
# there, and a condition may still want it. Only the rendered copy drops it.
PROMPT_HIDDEN_CONTEXT_KEYS = SECRET_CONTEXT_KEYS | {DOCUMENT_REF_KEY}

BILLING_USER = "user"
BILLING_SERVICE = "service"

# Free-tier and shared providers rate-limit and hiccup: HTTP 429, 5xx, connection resets.
# These are transient by nature - the very same call succeeds seconds later - so they retry
# inside the node's normal attempt budget instead of failing the whole run. A run whose
# compare node died on a 429 produced no output variable at all, and every downstream node
# then answered from nothing. Auth and permission errors stay fatal: retrying them just
# burns quota and hides a configuration problem.
TRANSIENT_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
TRANSIENT_RETRY_DELAYS = (2.0, 5.0)  # seconds; pause before retry 1 and retry 2


# --- Deterministic ACTION nodes: metadata "compare" replaces the model call ---------
# An ACTION node whose metadata carries a "compare" object performs a generic, purely
# mechanical comparison of two array variables and writes the result to the node's
# output_variable - no model call, so the step is exact, free, and immune to the copying
# mistakes LLM nodes kept making (dropped rows, reshuffled hours, invented entries). The
# engine learns nothing about any particular report format: what to match on, where the
# numbers live and what the statuses are called all come from the node's metadata (see
# README "Deterministic nodes").
MATCH_FIELDS_DEFAULT = "person,date"


def _parse_llm_array(text):
    """Parse a model-written JSON array variable.

    Accepts a plain array, one wrapped in ``` fences, a pretty-printed one, and NDJSON
    (objects one per line, brackets forgotten) - every shape real runs produced. Anything
    else raises ValueError with a short reason.
    """
    clean = str(text).strip()
    if clean.startswith("```"):
        clean = "\n".join(l for l in clean.splitlines()
                          if not l.strip().startswith("```")).strip()
    try:
        data = json.loads(clean)
        if isinstance(data, list):
            return data
    except json.JSONDecodeError:
        pass
    rows = []
    for line in clean.splitlines():
        line = line.strip().rstrip(",")
        if line.startswith("{"):
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    if rows:
        return rows
    raise ValueError("не массив и не построчный JSON (нет ни [ ], ни объектов на строках)")


def _coerce_number(value):
    """Hours arrive as numbers from the parsing nodes; a stray "4,5" / "4h" is still 4.5."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", ".")
    m = re.search(r"\d+(?:\.\d+)?", text)
    return float(m.group(0)) if m else None


def _match_value(field, value):
    """Canonical form of one match-field value.

    Text keys are case-insensitive (one report spells «Новиков», another «новиков»); a
    `date` field accepts both 2026-10-04 and 04.10.2026 spellings in the same key, so two
    reports that write the same day differently still pair.
    """
    text = str(value if value is not None else "").strip()
    if field.lower() == "date":
        m = re.fullmatch(r"(\d{2})\.(\d{2})\.(\d{4})", text)
        if m:
            return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    return text.casefold()


def _parse_java_map_string(text):
    """Parse a Java toString() map: "{left=doc1, right=doc2}" - with nested maps.

    The gRPC transport types node metadata as map<string, string> (langgraph.proto), so a
    nested object a client sent arrives stringified. Older Java builds used the default
    Map.toString() spelling ("{left=doc1}"); parsing it keeps already-persisted graph
    definitions alive.
    """
    inner = text.strip()
    if not (inner.startswith("{") and inner.endswith("}")):
        raise ValueError("не карта")
    inner = inner[1:-1].strip()
    parts, depth, cur = [], 0, []
    for ch in inner:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    if cur:
        parts.append("".join(cur).strip())

    # A plain value may itself contain a comma: match_fields=person,date arrives as the
    # fragments "match_fields=person" + "date". A fragment without "=" is the tail of the
    # previous value - glue it back instead of dropping it.
    whole = []
    for part in parts:
        if "=" in part or not whole:
            whole.append(part)
        else:
            whole[-1] = whole[-1] + "," + part

    out = {}
    for part in whole:
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        key, value = key.strip(), value.strip()
        if value.startswith("{") and value.endswith("}"):
            out[key] = _parse_java_map_string(value)
        elif value.startswith("'") and value.endswith("'"):
            out[key] = value[1:-1]
        else:
            out[key] = value
    return out


def _normalize_compare_config(raw):
    """Accept the compare config in every shape the transport can deliver.

    A dict (direct use), a JSON object as a string (the current Java mapper serialises
    nested metadata values to JSON), or the older Java Map.toString() spelling.
    Returns a dict or None if the shape is not recognisable.
    """
    if isinstance(raw, dict):
        return dict(raw)
    text = str(raw or "").strip()
    if not text:
        return None
    if text.startswith("{"):
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
        try:
            return _parse_java_map_string(text)
        except ValueError:
            return None
    return None


def _deterministic_compare(n_id: str, n_meta: Dict[str, Any],
                           state: GraphState) -> Optional[Dict[str, Any]]:
    """The update dict for a `compare`-configured ACTION node, or None (not configured).

    Pairs records of the `left` array with records of the `right` array on the combined
    `match_fields` key, positionally within equal keys (first unused right record), then
    classifies: equal hours -> ok, different -> mismatch, left record without a partner ->
    left_only, right record without a partner -> right_only. One record enters exactly one
    output object - by construction, not by instruction.
    """
    config = _normalize_compare_config((n_meta or {}).get("compare"))
    if config is None:
        return None

    left_name = str(config.get("left") or "doc1").strip() or "doc1"
    right_name = str(config.get("right") or "doc2").strip() or "doc2"
    fields = [f.strip() for f in str(config.get("match_fields") or MATCH_FIELDS_DEFAULT)
              .split(",") if f.strip()] or ["person", "date"]
    hours_left_field = str(config.get("left_hours_field")
                           or config.get("hours_field") or "hours")
    hours_right_field = str(config.get("right_hours_field")
                            or config.get("hours_field") or "hours")
    task_field = str(config.get("task_field") or "task")
    names = config.get("status_names") or {}
    s_ok = str(names.get("ok") or "ok")
    s_mismatch = str(names.get("mismatch") or "hours_mismatch")
    s_left_only = str(names.get("left_only") or "only_in_1")
    s_right_only = str(names.get("right_only") or "only_in_2")
    out_var = str((n_meta or {}).get("output_variable") or "matched").strip() or "matched"

    variables = state.get("variables") or {}
    missing = [name for name, value in ((left_name, variables.get(left_name)),
                                        (right_name, variables.get(right_name)))
               if not str(value or "").strip()]
    if missing:
        return {
            "last_node": n_id,
            "output": "Error: deterministic compare needs the variables "
                      + ", ".join(missing)
                      + " (the nodes that produce them did not run or wrote nothing).",
            "error": f"missing_variables:{','.join(missing)}",
        }
    try:
        left = _parse_llm_array(variables[left_name])
        right = _parse_llm_array(variables[right_name])
    except ValueError as e:
        return {
            "last_node": n_id,
            "output": f"Error: cannot parse the arrays to compare - {e}",
            "error": f"unparseable_arrays:{e}",
        }

    def key_of(rec):
        return tuple(_match_value(f, rec.get(f)) for f in fields)

    def title_tokens(rec):
        # Words of the human-readable task text, lower-cased, length >= 4 - the overlap of
        # two systems' titles for the same work. Numbers and task keys count too: an
        # identical task id is the strongest signal there is.
        text = str(rec.get(task_field) or "").casefold()
        return {t for t in re.findall(r"[a-zа-яё0-9]+", text) if len(t) >= 4}

    index = {}
    for i, rec in enumerate(right):
        index.setdefault(key_of(rec), []).append(i)

    # Pair CLUSTER BY CLUSTER (one combined key at a time), never globally: the same
    # person+date is the evidence the two rows are the same work. A 1-on-1 cluster pairs
    # unconditionally - one row per side is the strongest signal, titles are noise. Larger
    # clusters pair greedily by the largest title-token overlap, because "first with first"
    # pairs the wrong rows whenever one side has an extra record; leftovers stay UNPAIRED
    # (only_in_* on each side) rather than being force-paired by position - a forced pair
    # invents a mismatch, and an unpaired pair only reports two honest absences.
    clusters_left = {}
    for i, rec in enumerate(left):
        clusters_left.setdefault(key_of(rec), []).append(i)

    used_right = set()
    partner_of = {}

    for key, l_ids in clusters_left.items():
        r_ids = [i for i in index.get(key, [])]
        free_r = [i for i in r_ids if i not in used_right]
        if len(l_ids) == 1 and len(free_r) == 1:
            partner_of[l_ids[0]] = free_r[0]
            used_right.add(free_r[0])
            continue
        scored = sorted(
            ((len(title_tokens(left[i]) & title_tokens(right[j])), i, j)
             for i in l_ids for j in free_r),
            key=lambda t: (-t[0], t[1], t[2]))
        for score, i, j in scored:
            if score == 0:
                break
            if i in partner_of or j in used_right:
                continue
            partner_of[i] = j
            used_right.add(j)

    objects = []
    for i, rec in enumerate(left):
        h1 = _coerce_number(rec.get(hours_left_field))
        partner = partner_of.get(i)
        if partner is None:
            objects.append({**{f: rec.get(f) for f in fields},
                            task_field: rec.get(task_field),
                            "hours_1": h1, "hours_2": None, "status": s_left_only})
            continue
        h2 = _coerce_number(right[partner].get(hours_right_field))
        objects.append({**{f: rec.get(f) for f in fields},
                        task_field: rec.get(task_field),
                        "hours_1": h1, "hours_2": h2,
                        "status": s_ok if (h1 is not None and h2 is not None
                                           and h1 == h2) else s_mismatch})
    for i, rec in enumerate(right):
        if i in used_right:
            continue
        objects.append({**{f: rec.get(f) for f in fields}, task_field: rec.get(task_field),
                        "hours_1": None, "hours_2": _coerce_number(rec.get(hours_right_field)),
                        "status": s_right_only})

    findings = [o for o in objects if o["status"] != s_ok]
    ordered = findings + [o for o in objects if o["status"] == s_ok]
    text = ("[\n" + ",\n".join(json.dumps(o, ensure_ascii=False) for o in ordered) + "\n]")
    pairs = len(objects) - len(findings)
    summary = (f"Детерминированное сопоставление (без модели): {len(left)} записей слева, "
               f"{len(right)} справа, пар {pairs}, расхождений {len(findings)}.")
    return {
        "last_node": n_id,
        "output": summary,
        "variables": {out_var: text},
        "total_tokens": 0,
        "llm_billing": "",
        "history": [{"node": n_id, "output": summary, "tokens_used": 0}],
    }


def _is_transient_llm_error(error: Exception) -> bool:
    """True for errors worth one more attempt: rate limits, provider hiccups, timeouts."""
    status = getattr(error, "status_code", None)
    if isinstance(status, int) and status in TRANSIENT_STATUS_CODES:
        return True
    text = str(error).lower()
    return any(token in text for token in (
        "429", "rate limit", "too many requests", "service unavailable", "overloaded",
        "temporarily", "timeout", "timed out", "connection",
    ))


def credentials_from_config(config: Optional[RunnableConfig]) -> LLMCredentialSet:
    """Personal keys carried by this run. Never raises (see LLMCredentialSet.from_json)."""
    configurable = (config or {}).get("configurable") or {}
    return LLMCredentialSet.from_json(configurable.get(CONFIG_LLM_CREDENTIALS))


def require_user_key_from_config(config: Optional[RunnableConfig]) -> bool:
    """True when the caller asked for hard billing (policy=REQUIRE) instead of fallback."""
    configurable = (config or {}).get("configurable") or {}
    return str(configurable.get(CONFIG_REQUIRE_USER_KEY, "")).strip() == "1"


def caller_kind_from_config(config: Optional[RunnableConfig]) -> str:
    """Who is asking: "" (an interactive session - the default wording) or ``"b2b"``.

    Only ever used to pick the phrasing of the "no key for this provider" failure: an
    integrator on the B2B facade must be told to send ``llm_credentials_json``, not to click
    a keys button in a UI it never sees. Set by the backend; a client cannot forge it (the
    run key is stripped from anything they send).
    """
    configurable = (config or {}).get("configurable") or {}
    return str(configurable.get(CONFIG_CALLER_KIND, "")).strip()


def document_ref_keys_from_config(config: Optional[RunnableConfig]) -> Set[str]:
    """The context keys this run's DOCUMENT nodes read, as the servicer resolved them.

    Absent (a test, a direct call, a legacy caller) means "nothing is known", so the caller
    falls back to the one key the contract has always had - a run must never become leaky
    just because the servicer did not get a chance to annotate it.
    """
    configurable = (config or {}).get("configurable") or {}
    raw = str(configurable.get(CONFIG_DOCUMENT_REF_KEYS) or "").strip()
    keys = {k.strip() for k in raw.split(",") if k.strip()} if raw else set()
    return keys or {DOCUMENT_REF_KEY}


def scrub_prompt_context(context: Any, hidden_keys: Optional[Set[str]] = None) -> Dict[str, Any]:
    """Copy of ``context`` that is safe to render into a prompt.

    Drops every key in :data:`PROMPT_HIDDEN_CONTEXT_KEYS` - the credential-shaped ones, which
    would leak a user's key to their model provider - plus ``hidden_keys``, the document
    references this graph's DOCUMENT nodes read. Not a secret filter alone: a reference is not
    secret, it is USELESS there, and showing it is what makes a model announce it cannot open a
    file that a DOCUMENT node has already turned into text. A custom-named reference is just as
    useless, so the graph's own keys are hidden too rather than the one default spelling.

    ``hidden_keys`` defaults to the default reference key, so a caller that knows nothing about
    the graph behaves exactly as before.
    """
    if not isinstance(context, dict):
        return {}
    hidden = PROMPT_HIDDEN_CONTEXT_KEYS | (hidden_keys or {DOCUMENT_REF_KEY})
    return {k: v for k, v in context.items() if k not in hidden}


def _include_variables(n_meta: Optional[Dict]) -> Optional[Set[str]]:
    """The variables a node declares it consumes, or ``None`` for "everything".

    Metadata ``include_variables`` is a comma-separated list. This is how a node in a
    pipeline narrows its own prompt: the node that compares two normalised tables names only
    those tables, so the two raw documents it must not re-parse are no longer in its prompt
    at all. Declaring nothing keeps the old behaviour - every variable is shown.
    """
    raw = str((n_meta or {}).get("include_variables") or "").strip()
    names = {n.strip() for n in raw.split(",") if n.strip()}
    return names or None


# Which clients accept a `credentials=` keyword. LazyLLMClient does; the older
# YandexGPTClient handed out by NodeHandler.default_llm does not, and passing it anyway
# would surface as a TypeError inside a running node. Memoised per class - the set of
# client types is small and fixed.
_CREDENTIALS_SUPPORTED: Dict[type, bool] = {}


def _accepts_credentials(client: Any) -> bool:
    client_type = type(client)
    cached = _CREDENTIALS_SUPPORTED.get(client_type)
    if cached is None:
        try:
            signature = inspect.signature(client.generate)
        except (TypeError, ValueError):
            cached = False
        else:
            cached = "credentials" in signature.parameters
        _CREDENTIALS_SUPPORTED[client_type] = cached
    return cached

# YandexGPT occasionally refuses a perfectly benign prompt ("не могу обсуждать эту тему") or
# answers with prose instead of the requested JSON. Such an answer is unusable for a node, so we
# re-ask once with a stricter, blunter instruction instead of failing the whole run. If the
# budget is spent and the answer is STILL prose (and not a data payload - see looks_like_data),
# the run stops with an explicit refusal message instead of feeding the refusal downstream:
# see LLMRefusalError / TerminalNodeError.
REFUSAL_MARKERS = (
    "не могу", "не могу обсуждать", "не буду", "не имею возможности", "извините",
    "к сожалению, я", "i can't", "i cannot", "i'm sorry", "i am unable", "as an ai",
)

RETRY_HINT = (
    "\n\nПредыдущий ответ не подошёл. Нужен только результат по задаче, без отказов, без "
    "пояснений и без markdown. Ответь строго в том формате, который описан выше, и не отказывайся."
)

# Same retry instruction for the other providers. The retry hint is appended to the node's
# system prompt, so a Russian instruction would be an odd (and usually counter-productive)
# addition to an English prompt sent to OpenAI / Anthropic / Groq / DeepSeek / OpenRouter.
RETRY_HINT_EN = (
    "\n\nThe previous answer was not usable. Reply only with the task result: no refusals, "
    "no apologies and no markdown. Answer strictly in the format described above and do not refuse."
)


def retry_hint_for(llm) -> str:
    """Provider-appropriate retry instruction.

    Russian for yandex (the historical behaviour) and for any client that does not expose a
    ``provider`` (the pre-multi-provider Yandex-only path), English for every other provider.
    """
    return RETRY_HINT_EN if getattr(llm, "provider", "") not in ("", "yandex") else RETRY_HINT


def is_unusable_answer(text: str) -> bool:
    """True when the LLM answer cannot be used by a node (refusal or empty output)."""
    if not text or not text.strip():
        return True
    lowered = text.lower()
    return any(marker in lowered for marker in REFUSAL_MARKERS)


def looks_like_data(text: str) -> bool:
    """True when an unusable-looking answer is still shaped like a data payload.

    The refusal markers are substring matches, and real payloads contain them: a task
    literally named «Не могу подтвердить отчёт» matches "не могу", and a normalisation
    table or a JSON array carrying that name is DATA, not a refusal. Such answers were
    handed downstream unchanged after the retry budget and still must be - so before a
    run is stopped, the shape decides: a JSON array/object, a fenced block or a
    markdown table is a payload; prose is a refusal (or an empty answer).
    """
    t = (text or "").lstrip()
    if not t:
        return False
    if t.startswith("```"):
        return True                      # fenced payload (json / code / table)
    if t[:1] in ("[", "{"):
        return True                      # json array / object
    return t.splitlines()[0].lstrip().startswith("|")   # markdown table


class TerminalNodeError(Exception):
    """A node cannot produce a usable result: the run must STOP here.

    The servicer turns it into a NODE_END frame (the `error` rides the frame for the
    run record) plus an END event whose output is ``user_message`` - the caller is
    told why the graph stopped instead of a downstream node answering from nothing
    and inventing a result (the "Расхождений не найдено" report built on a refusal
    that poisoned ``doc1`` - the exact failure this exists for). ``error_code`` is the
    machine-readable form; it also goes to the logs and the NODE_END frame.
    """

    def __init__(self, *, error_code: str, user_message: str = "",
                 llm_billing: str = "", total_tokens: int = 0):
        super().__init__(error_code)
        self.error_code = error_code
        self.user_message = user_message
        self.llm_billing = llm_billing
        self.total_tokens = total_tokens
        # Filled by the node handler that lets it through - only the handler knows
        # which node it was (and, for subgraphs, which OUTER node carries the frame).
        self.node_id = ""
        self.node_label = ""


class LLMRefusalError(TerminalNodeError):
    """The model refused (safety filter / policy) or answered empty on every attempt."""

    def __init__(self, *, provider: str, model: str, attempts: int, answer: str,
                 llm_billing: str = "", total_tokens: int = 0):
        super().__init__(error_code=f"llm_refusal:{provider or 'unknown'}",
                         llm_billing=llm_billing, total_tokens=total_tokens)
        self.provider = provider
        self.model = model
        self.attempts = attempts
        self.answer = answer or ""


def refusal_message(exc: LLMRefusalError) -> str:
    """The user-facing text for a refusal - it becomes the graph's final output.

    Russian on purpose: the message is bound for the end user's chat (the graph's
    prompts and the deterministic compare summary are Russian too), and it must say
    not only THAT the model refused but WHY STOPPING was the only honest option -
    a run continued without the model's data ends in an invented result.
    """
    who = f"{exc.provider}/{exc.model}" if exc.model else (exc.provider or "модель")
    where = f" (узел «{exc.node_label}»)" if exc.node_label else ""
    if exc.answer.strip():
        quote = " ".join(exc.answer.split())
        if len(quote) > 300:
            quote = quote[:297] + "..."
        head = (f"⛔ Модель {who} отказалась обрабатывать запрос "
                f"(фильтр безопасности провайдера){where}, попыток: {exc.attempts}. "
                f"Ответ модели: «{quote}».")
    else:
        head = (f"⛔ Модель {who} вернула пустой ответ{where} "
                f"после {exc.attempts} попыток.")
    return (head +
            " Граф остановлен — без ответа модели продолжать нельзя: "
            "дальнейшие шаги выдали бы недостоверный результат.")


HISTORY_MARKER = "=== CONVERSATION HISTORY ==="
VARIABLES_MARKER = "=== CURRENT STATE VARIABLES ==="


def drop_history_block(user_message: str) -> str:
    """Remove the conversation-history block from an already formatted state context."""
    if HISTORY_MARKER not in user_message or VARIABLES_MARKER not in user_message:
        return user_message
    head, _, rest = user_message.partition(HISTORY_MARKER)
    _, _, tail = rest.partition(VARIABLES_MARKER)
    return head + VARIABLES_MARKER + tail


# A bare answer counts as a value only when it is a single word token: letters of any script,
# digits, underscore, hyphen, and at most one decimal point. Everything else - truncated JSON,
# stray punctuation, prose - is not a value, and storing it as a variable would only produce a
# second silent no-match instead of the parse error that tells the operator what went wrong.
_WORD_TOKEN = re.compile(r"[\w\-]+(?:\.[\w\-]+)*")


def parse_scalar_answer(text: str, keys: Set) -> Dict[str, Any]:
    """Reads a bare (non-JSON) answer as the value of a single-key router.

    A router expecting exactly one key must not fail because the model replied ``true`` instead of
    ``{"output": true}`` - that is precisely the prompt micro-management this engine should not
    require. Quoted text may contain spaces; unquoted text must be a single word token, so
    truncated JSON, punctuation and prose are rejected instead of being stored as a variable that
    silently matches nothing. Multi-key routers need the object form and raise.
    """
    if len(keys) != 1:
        raise JsonGenerationError(
            f"Expected a JSON object with keys {sorted(keys)}, got a bare answer instead: "
            f"{(text or '')[:100]!r}"
        )

    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        cleaned = "\n".join(lines[1:-1]).strip() if len(lines) > 2 else cleaned.strip("`").strip()

    if not cleaned or "\n" in cleaned or is_unusable_answer(cleaned):
        raise JsonGenerationError(f"Answer is not a usable value: {(text or '')[:100]!r}")

    quoted = len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in ("'", '"')
    if quoted:
        value: Any = cleaned[1:-1]
    elif not _WORD_TOKEN.fullmatch(cleaned) or len(cleaned) > 40:
        raise JsonGenerationError(f"Bare answer is not a single value: {cleaned[:100]!r}")
    else:
        value = parse_literal(cleaned)

    return {next(iter(keys)): value}


class NodeHandler:
    # Per-node context size control (metadata "context_mode"). A node usually needs only part of
    # the state, and a smaller prompt is both cheaper and noticeably less likely to be refused:
    #   "minimal" -> variables only (no conversation history, no raw http_result)
    #   "slim"   -> variables incl. http_result, no conversation history
    #   "trim"   -> conversation history + variables, no raw http_result
    #   absent   -> everything (back-compat default)
    CONTEXT_MODES = {
        "minimal": (False, False),
        "slim": (False, True),
        "trim": (True, False),
        "full": (True, True),
    }

    # Node types this handler knows how to dispatch. Anything else is a corrupt or legacy
    # definition (for example a node type that was renamed in the API) and must fail loudly,
    # because the former fall-through turned such a node into a silent LLM ACTION node: the HTTP
    # call simply never happened and nothing reported it.
    # START / END / HUMAN are pass-through ACTION nodes here - they carry no prompt of their own.
    ACTION_NODE_TYPES = frozenset({"START", "END", "ACTION", "HUMAN"})
    KNOWN_NODE_TYPES = ACTION_NODE_TYPES | {"GRAPH", "HTTP_REQUEST", "DOCUMENT"}

    def __init__(self, graph_loader: GraphLoader, default_llm=None, default_llm_factory=None,
                 document_fetcher=None):
        # Each node may carry its own provider/model: the servicer resolves a client per
        # node (node metadata -> graph config -> service default) and passes it into
        # process(). The service-level fallback below is built lazily so that a missing
        # API key never prevents the server from starting up - it surfaces on first use.
        self.default_llm = default_llm
        self.default_llm_factory = default_llm_factory
        self.graph_loader = graph_loader
        # Injected by the servicer in commit 3 of this feature: an async callable
        # (document_id) -> (bytes, filename, mime_type) backed by the Java source of truth.
        # A DOCUMENT node with no fetcher fails loudly at run time instead of silently
        # producing an empty prompt - the same contract as an unreachable HTTP service.
        self.document_fetcher = document_fetcher

        try:
            self.http_channel = grpc.aio.insecure_channel(
                f'{settings.HTTP_REQUEST_SERVICE_HOST}:{settings.HTTP_REQUEST_SERVICE_PORT}'
            )
            self.http_stub = langgraph_pb2_grpc.HttpRequestServiceStub(self.http_channel)
            logger.info(f"Connected to HTTP Request Service at {settings.HTTP_REQUEST_SERVICE_HOST}:{settings.HTTP_REQUEST_SERVICE_PORT}")
        except Exception as e:
            logger.error(f"Failed to connect to HTTP Request Service: {e}")
            self.http_stub = None

    def _context_flags(self, n_meta: Dict) -> tuple[bool, bool]:
        mode = str((n_meta or {}).get("context_mode", "full")).strip().lower()
        return self.CONTEXT_MODES.get(mode, self.CONTEXT_MODES["full"])

    def _default_client(self):
        """Service-level fallback client, resolved on first use and then memoised."""
        if self.default_llm is None and self.default_llm_factory is not None:
            self.default_llm = self.default_llm_factory()
        return self.default_llm

    async def _generate(self, user_message: str, system_message: str, llm=None,
                        credentials: Optional[LLMCredentialSet] = None,
                        require_user_key: bool = False,
                        caller_kind: str = "") -> Tuple[Any, str]:
        """Call the LLM, retrying once when the answer is a refusal or empty text.

        After the retry budget is spent, an answer that is still prose raises
        ``LLMRefusalError`` (the run stops with an explicit message - see
        TerminalNodeError); an answer that is a data payload (looks_like_data) is
        returned as-is, refusal markers inside real data notwithstanding.

        Transient provider failures (HTTP 429 / 5xx, timeouts, connection resets) retry on
        the same budget with a short pause instead of failing the node - a rate limit says
        nothing about the prompt, and a node that dies on one leaves downstream nodes with
        no variable to work from. On the retry we also shrink the prompt: the conversation
        history is dropped and a stricter instruction is appended, because a long context is
        what most often triggers the refusal in the first place.

        ``credentials`` are this run's personal keys, indexed by provider; the entry picked
        is the one matching the provider *this client* resolved to, so a mixed-provider graph
        spends each user's key only on the providers that user actually owns a key for.

        Returns ``(response, billing_owner)`` so the caller can report who paid:
        ``"user"`` (a personal key), ``"service"`` (the shared config-based key). Under the
        default FALLBACK policy a missing personal key is not an error - it is simply a
        service-billed call; with ``require_user_key`` - or when the shared key is switched
        off entirely (``LLM_SERVICE_KEY_ENABLED=false``) - it raises first instead, and
        ``caller_kind`` words that failure for whoever will read it (an integrator on the
        B2B facade is told to send ``llm_credentials_json``, not to click a UI button).
        """
        client = llm or self._default_client()
        if client is None:
            raise RuntimeError("No LLM client resolved for this node (missing provider config).")

        provider = str(getattr(client, "provider", "") or "")
        supports_credentials = _accepts_credentials(client)
        credential: Optional[LLMCredentials] = (
            credentials.for_provider(provider) if credentials is not None else None
        )

        # A personal key can only be spent through a client that accepts one (a legacy
        # client with no `credentials` kwarg bills whatever it was built with); a client
        # already configured with a personal key - billing_owner == "user" - counts too.
        # Anything else means the SHARED service key would pay, which policy=REQUIRE
        # forbids, so this fails closed instead of silently subsidising the run.
        personal_key_available = (
            (credential is not None and not credential.is_empty() and supports_credentials)
            or getattr(client, "billing_owner", "") == BILLING_USER
        )
        if require_user_key and not personal_key_available:
            raise MissingUserApiKeyError(
                missing_user_key_message(provider or "default", caller_kind))
        if not settings.LLM_SERVICE_KEY_ENABLED and not personal_key_available:
            # The kill switch is checked HERE as well as in LLMClient._api_key: raising here
            # gives the same failure the audience-appropriate wording (the hard gate inside
            # the client stays as the guarantee for any path that reaches a client directly).
            raise MissingUserApiKeyError(
                missing_user_key_message(provider or "default", caller_kind))

        billing = BILLING_USER if personal_key_available else BILLING_SERVICE

        generate_kwargs: Dict[str, Any] = {}
        if supports_credentials:
            generate_kwargs["credentials"] = credential

        attempts = max(1, settings.LLM_MAX_ATTEMPTS)
        prompt = system_message
        message = user_message
        response = None
        for attempt in range(1, attempts + 1):
            try:
                response = await client.generate(user_message=message, system_message=prompt,
                                                 **generate_kwargs)
            except Exception as e:
                # A 429 / provider hiccup says nothing about the prompt - the same call goes
                # through seconds later, so spend one of this node's attempts on the wait
                # rather than let the whole run die and the downstream nodes answer from
                # nothing. Non-transient failures (bad key, permission) re-raise at once.
                if not _is_transient_llm_error(e) or attempt == attempts:
                    raise
                delay = TRANSIENT_RETRY_DELAYS[min(attempt, len(TRANSIENT_RETRY_DELAYS)) - 1]
                logger.warning("LLM call hit a transient error (attempt %d/%d): %s - "
                               "retrying in %.0fs", attempt, attempts, e, delay)
                await asyncio.sleep(delay)
                continue
            # str(LLMResponse) is the text itself; a raw string response passes through
            # unchanged. Extract .content explicitly so test doubles that return a
            # SimpleNamespace are read as their payload, not as their repr.
            text = str(getattr(response, "content", response))
            if not is_unusable_answer(text):
                return response, billing
            if attempt == attempts:
                logger.warning("LLM returned an unusable answer after %d attempt(s): %s",
                               attempt, text[:120].replace("\n", " "))
                if looks_like_data(text):
                    # Marker words INSIDE a real payload (a task named «Не могу
                    # подтвердить отчёт» inside a JSON array or a table) are data, not
                    # a refusal: hand the payload back unchanged, as before.
                    return response, billing
                # Still prose after the whole retry budget: a refusal or an empty answer.
                # Passing it on would poison output_variable and let downstream nodes
                # answer from nothing - the run stops with an explicit message instead.
                usage = getattr(response, "usage", {})
                raise LLMRefusalError(
                    provider=provider or "unknown",
                    model=str(getattr(client, "model", "") or ""),
                    attempts=attempt,
                    answer=text,
                    llm_billing=billing,
                    total_tokens=usage.get("total_tokens", 0) if isinstance(usage, dict) else 0,
                )
            logger.warning("LLM refused or returned an empty answer (attempt %d), retrying", attempt)
            prompt = system_message + retry_hint_for(client)
            message = drop_history_block(user_message)
        return response, billing

    async def process(self, n_id: str, n_meta: Dict, node_type: str, is_router_node: bool,
                      router_keys: Set, state: GraphState, config: RunnableConfig,
                      llm=None) -> Dict:
        # Resolved once per node invocation from the run's config: the compiled graph is
        # shared by every user, so the caller's keys can only arrive per execution.
        credentials = credentials_from_config(config)
        require_user_key = require_user_key_from_config(config)
        caller_kind = caller_kind_from_config(config)

        if is_router_node:
            return await self._process_router(n_id, n_meta, router_keys, state, llm,
                                              credentials, require_user_key, caller_kind)
        if node_type == "GRAPH":
            return await self._process_subgraph(n_id, n_meta, state, config)
        if node_type == "HTTP_REQUEST":
            return await self._process_http_request(n_id, n_meta, state)
        if node_type == "DOCUMENT":
            # llm/credentials ride along for `normalize: "llm"`: the mapping proposal is an
            # ordinary model call made with THIS run's client and keys. A node that does not
            # ask for the mode never uses them - and the servicer only resolved a client for
            # it in that case (see uses_llm), so passing them costs nothing here.
            return await self._process_document(n_id, n_meta, state, llm,
                                                credentials, require_user_key, caller_kind)
        if node_type in self.ACTION_NODE_TYPES:
            return await self._process_action(n_id, n_meta, state, llm,
                                              credentials, require_user_key, caller_kind,
                                              document_ref_keys_from_config(config))

        raise ValueError(
            f"Node {n_id}: unknown node type '{node_type}'. "
            f"Known types: {', '.join(sorted(self.KNOWN_NODE_TYPES))}"
        )

    async def _process_router(self, n_id: str, n_meta: Dict[str, Any], router_keys: Set,
                              state: GraphState, llm=None,
                              credentials: Optional[LLMCredentialSet] = None,
                              require_user_key: bool = False,
                              caller_kind: str = "") -> Dict[str, Any]:
        data_block = format_state_context(state, *self._context_flags(n_meta))
        system_prompt = n_meta.get("system_prompt", "You are a helpful assistant.")
        keys_list = ", ".join(router_keys) if router_keys else "intent"

        # A router answers with the keys the outgoing edges are conditioned on - and only those.
        # The engine merges the answer into `variables` itself (see below), so the model used to be
        # told to echo the whole state back: on the first turn of a chat run that state can hold
        # every subscriber (names, telegram ids), and that echo became the node's visible output -
        # in Orchestra it was then read as "the question" and sent to the chat.
        system_prompt = (
            f"{system_prompt}\n\n"
            f"You are a routing classifier. You must determine the next step. "
            f"Determine the value for exactly these key(s): [{keys_list}]. "
            f"Output ONLY a valid JSON object containing those key(s) - no other keys, "
            f"no prose, no explanation. Do not repeat or copy the existing state variables: "
            f"the run keeps them itself."
        )

        try:
            llm_response, billing = await self._generate(
                user_message=data_block, system_message=system_prompt, llm=llm,
                credentials=credentials, require_user_key=require_user_key,
                caller_kind=caller_kind)

            # Safely extract content and tokens regardless of whether llm_response is a string or an object
            output_content = getattr(llm_response, 'content', str(llm_response))
            usage = getattr(llm_response, 'usage', {})
            tokens = usage.get('total_tokens', 0) if isinstance(usage, dict) else 0

            try:
                parsed_data = extract_json_from_response(output_content)
            except JsonGenerationError:
                # Single-key routers also accept a bare answer ("true", "4", "billing"): forcing
                # the model to spell {"output": true} is prompt micro-management, and bailing out
                # here used to leave `variables` untouched, so no outgoing edge could ever match.
                parsed_data = parse_scalar_answer(output_content, router_keys)
                logger.info(f"Router {n_id} accepted a bare scalar answer: {parsed_data}")

            # The JSON parsed, but did it answer the keys the edges are conditioned on? A model that
            # returns {answer: true} while the edges wait on `output` is about to end the run with no
            # branch taken - naming the gap here puts the cause in the log at the moment it happens,
            # instead of leaving only "no condition matched" further down the pipeline.
            missing = {str(key) for key in router_keys} - {str(key) for key in parsed_data}
            if missing:
                logger.warning(
                    f"Router {n_id} answered without the expected key(s) {sorted(missing)}; "
                    f"got {sorted(str(key) for key in parsed_data)}. Edges conditioned on those "
                    f"keys cannot match."
                )

            current_vars = state.get("variables", {}) or {}
            merged_vars = {**current_vars, **parsed_data}

            return {
                "last_node": n_id,
                "output": output_content,
                "variables": merged_vars,
                "total_tokens": tokens,
                "llm_billing": billing,
                "history": [{"node": n_id, "output": output_content, "tokens_used": tokens}]
            }
        except LLMRefusalError as e:
            # A router answers JSON; a refusal or an empty answer means no branch keys
            # can exist. Ending the run with an explanation beats "no condition matched"
            # plus the raw refusal as the run's output.
            e.node_id = n_id
            e.node_label = str((n_meta or {}).get("label") or "")
            e.user_message = refusal_message(e)
            raise
        except MissingUserApiKeyError as e:
            # An expected, actionable condition under policy=REQUIRE: a warning plus a
            # readable node output, not a stack dump - the message already says where to
            # add the key, and the run stops instead of silently billing the service.
            # llm_billing stays "" on purpose: NO model was called, so nothing was billed -
            # reporting "service" here would imply a spend that never happened.
            logger.warning("Router %s stopped: %s", n_id, e)
            return {"last_node": n_id, "output": str(e), "error": str(e),
                    "llm_billing": ""}
        except JsonGenerationError as e:
            logger.error(f"Router {n_id} failed to parse JSON: {e}")
            return {"last_node": n_id, "output": str(llm_response), "error": str(e)}
        except Exception as e:
            logger.error(f"Node execution failed: {e}", exc_info=True)
            return {"last_node": n_id, "output": f"Error: {str(e)}",
                    "history": [{"node": n_id, "output": f"Error: {str(e)}"}]}

    async def _process_action(self, n_id: str, n_meta: Dict[str, Any], state: GraphState,
                              llm=None, credentials: Optional[LLMCredentialSet] = None,
                              require_user_key: bool = False,
                              caller_kind: str = "",
                              document_ref_keys: Optional[Set[str]] = None) -> Dict[str, Any]:
        # A node can skip the model entirely: metadata "compare" turns this ACTION into a
        # deterministic array-vs-array comparison (see _deterministic_compare). Checked
        # before the prompt handling because such a node legitimately has no prompt at all.
        deterministic = _deterministic_compare(n_id, n_meta, state)
        if deterministic is not None:
            if str(deterministic.get("error") or "").startswith("unparseable_arrays"):
                # The inputs EXIST but are garbage: nothing downstream can fix that, and
                # the report node, answering from nothing, would invent a result («0
                # расхождений» built on unparseable data). Missing variables stay a
                # walk-past error on purpose - the attachment flow relies on the run
                # continuing and re-asking for the file.
                label = str((n_meta or {}).get("label") or "")
                reason = str(deterministic.get("error") or "").partition(":")[2]
                stop = TerminalNodeError(
                    error_code=str(deterministic.get("error")),
                    user_message=(
                        "⛔ Данные для сравнения не удалось разобрать"
                        + (f" (узел «{label}»)" if label else "")
                        + (f": {reason}." if reason else ".")
                        + " Граф остановлен — без корректных данных отчёт выдал бы "
                          "недостоверный результат."
                    ),
                )
                stop.node_id = n_id
                stop.node_label = label
                raise stop
            return deterministic

        user_input = state.get("input") or ""
        if not user_input.strip():
            return {"last_node": n_id, "output": "No input provided."}

        system_prompt = n_meta.get("system_prompt")
        if not system_prompt:
            logger.info(f"Node {n_id} is pass-through (no prompt).")
            return {"last_node": n_id}

        data_block = format_state_context(state, *self._context_flags(n_meta),
                                          only_variables=_include_variables(n_meta))
        final_user_input = data_block

        # state["context"] is rendered straight into the prompt, so anything credential-like
        # is dropped here as well (the servicer already popped the keys the backend sent; this
        # covers a client that put its own llm_api_key into the context map). The document
        # handle goes with them: a model cannot open a file, and being shown the reference
        # only makes it say so - the DOCUMENT node has already turned the file into text. A
        # slot may name its reference key whatever it likes, so the keys to hide come from the
        # run (see document_ref_keys_from_config) rather than being one fixed spelling.
        context_data = scrub_prompt_context(state.get("context"), document_ref_keys)
        if context_data:
            try:
                context_str = json.dumps(context_data, indent=2, default=str)
                final_user_input += f"\n\n=== STATIC CONTEXT ===\n{context_str}"
            except Exception as e:
                logger.warning(f"Failed to serialize context for node {n_id}: {e}")

        try:
            llm_response, billing = await self._generate(
                user_message=final_user_input, system_message=system_prompt, llm=llm,
                credentials=credentials, require_user_key=require_user_key,
                caller_kind=caller_kind)

            output_content = getattr(llm_response, 'content', str(llm_response))
            usage = getattr(llm_response, 'usage', {})
            tokens = usage.get('total_tokens', 0) if isinstance(usage, dict) else 0

            update = {
                "last_node": n_id,
                "output": output_content,
                "history": [{"node": n_id, "output": output_content, "tokens_used": tokens}],
                "total_tokens": tokens,
                "llm_billing": billing
            }
            # An ACTION node may publish its answer under a variable name of its own, so the
            # next node can read it as DATA instead of re-deriving it. Without this a node's
            # answer only ever lives in `output`, which the next node overwrites - so a
            # multi-step pipeline cannot pass an intermediate result forward at all. This is
            # what makes a "normalise, then compare" graph work: the first node writes its
            # table to `variables[output_variable]`, and the second works from that table
            # rather than re-parsing the two source documents.
            #
            # `output` is still set to the same text, so the run's final answer, the history
            # entry and the state inspector all keep showing what the node said.
            output_variable = str((n_meta or {}).get("output_variable") or "").strip()
            if output_variable:
                update["variables"] = {output_variable: output_content}
            return update
        except LLMRefusalError as e:
            if e.answer.strip() and not str((n_meta or {}).get("output_variable") or "").strip():
                # A prose node's answer IS the output: the model's own words reached the
                # caller that way before and still must - wrapping them would swallow
                # answers that merely QUOTE refusal phrases («Не могу подтвердить...»
                # inside a report). An empty answer has nothing to pass on and falls
                # through to the terminal stop below.
                return {"last_node": n_id, "output": e.answer,
                        "history": [{"node": n_id, "output": e.answer,
                                     "tokens_used": e.total_tokens}],
                        "total_tokens": e.total_tokens, "llm_billing": e.llm_billing}
            e.node_id = n_id
            e.node_label = str((n_meta or {}).get("label") or "")
            e.user_message = refusal_message(e)
            raise
        except MissingUserApiKeyError as e:
            # Refused, so no model call and no tokens: llm_billing must stay "" (see the
            # router branch) rather than claim the service paid for a run that never spent.
            logger.warning("Node %s stopped: %s", n_id, e)
            return {"last_node": n_id, "output": str(e), "error": str(e),
                    "llm_billing": "",
                    "history": [{"node": n_id, "output": str(e)}]}
        except Exception as e:
            logger.error(f"Node execution failed: {e}", exc_info=True)
            return {"last_node": n_id, "output": f"Error: {str(e)}",
                    "history": [{"node": n_id, "output": f"Error: {str(e)}"}]}

    async def _process_subgraph(self, n_id: str, n_meta: Dict, state: GraphState, config: RunnableConfig) -> Dict[
        str, Any]:
        subgraph_id = n_meta.get("subgraph_id")
        if not subgraph_id:
            raise ValueError(f"Node {n_id} is type GRAPH but missing 'subgraph_id' in metadata.")

        logger.info(f"Node {n_id}: Loading subgraph {subgraph_id}")
        subgraph_runnable = await self.graph_loader(subgraph_id)

        parent_configurable = config.get("configurable") or {}
        parent_thread_id = parent_configurable.get("thread_id", "default")
        sub_thread_id = f"{parent_thread_id}__{subgraph_id}"

        # A subgraph's nodes resolve their OWN clients from *their* config, so the caller's
        # credentials have to be forwarded here. Dropping them (as a bare thread_id would)
        # means every node inside the subgraph silently bills the shared service key while
        # the rest of the run bills the user - the exact leak this feature exists to stop.
        sub_configurable: Dict[str, Any] = {"thread_id": sub_thread_id}
        for key in INHERITED_CONFIGURABLE_KEYS:
            value = parent_configurable.get(key)
            if value not in (None, ""):
                sub_configurable[key] = value
        sub_config: RunnableConfig = {"configurable": sub_configurable}

        sub_snapshot = await subgraph_runnable.aget_state(sub_config)

        try:
            if sub_snapshot and sub_snapshot.next:
                logger.info(f"Subgraph {subgraph_id} detected as paused. Updating state and resuming...")
                await subgraph_runnable.aupdate_state(sub_config, {"input": state.get("input")})
                result_state = await subgraph_runnable.ainvoke(None, config=sub_config)
            else:
                logger.info(f"Subgraph {subgraph_id} starting fresh.")
                result_state = await subgraph_runnable.ainvoke(state, config=sub_config)

            # The subgraph's last node already recorded who paid; forwarding it keeps the
            # parent's per-node event honest about what that node's subtree cost.
            sub_billing = result_state.get("llm_billing", "") \
                if isinstance(result_state, dict) else ""

            return {
                "last_node": n_id,
                "output": result_state.get("output", "Subgraph completed."),
                "variables": result_state.get("variables", {}),
                "history": result_state.get("history", []),
                "total_tokens": result_state.get("total_tokens", -1),
                "llm_billing": sub_billing
            }
        except GraphInterrupt:
            logger.info(f"Node {n_id}: Subgraph '{subgraph_id}' requested a pause. Propagating interrupt...")
            raise
        except LLMRefusalError as e:
            # The inner graph already aborted at the refusing node (ainvoke raised); the
            # frame belongs to THIS graph's node, so relabel it - the inner label stays
            # in the message, it says which nested step actually refused.
            e.node_id = n_id
            raise
        except Exception as e:
            logger.error(f"Subgraph execution failed in {n_id}: {e}", exc_info=True)
            return {"last_node": n_id, "output": f"Subgraph Error: {str(e)}", "error": str(e)}

    async def _process_http_request(self, n_id: str, n_meta: Dict, state: GraphState) -> Dict:
        if not self.http_stub:
            return {
                "last_node": n_id,
                "output": "Error: HTTP Request Service is not connected.",
                "error": "HTTP Request Service Unavailable",
                "variables": {**state.get("variables", {}), "http_success": False, "http_error": "Service Unavailable"}
            }

        logger.info(f"Node {n_id}: Executing HTTP request...")
        try:
            method = n_meta.get("method", "GET").upper()
            url = n_meta.get("url", "")

            raw_headers = n_meta.get("headers", "{}")
            try:
                headers_dict = json.loads(raw_headers) if isinstance(raw_headers, str) else raw_headers
            except (json.JSONDecodeError, TypeError):
                logger.warning(f"Node {n_id}: metadata 'headers' is not valid JSON, sending none")
                headers_dict = {}
            if not isinstance(headers_dict, dict):
                logger.warning(f"Node {n_id}: metadata 'headers' is not a JSON object, sending none")
                headers_dict = {}

            body = n_meta.get("body", "")
            variables = state.get("variables", {})

            request = langgraph_pb2.HttpRequestInput(
                method=method,
                url=url,
                headers=headers_dict,
                body=body,
                state_json=json.dumps(variables, default=str)
            )

            response = await self.http_stub.ExecuteHttpRequest(request)

            if response.success:
                result_data = {}
                if response.body:
                    try:
                        result_data = json.loads(response.body)
                    except json.JSONDecodeError:
                        result_data = {"raw_response": response.body}

                new_vars = {
                    **variables,
                    "http_result": result_data,
                    "http_success": True,
                    "http_status_code": response.status_code
                }
                # A node may keep its response under a name of its own, beside the historical
                # `http_result` (which stays, so every existing graph and router is untouched).
                # Without it a graph can make exactly one authenticated call per chain: the
                # tracker's login response lands in `http_result`, the very next call overwrites
                # it with a project list, and a later node's `{{http_result.token}}` then renders
                # an empty Authorization header - a 401 with nothing in the graph to explain it.
                result_variable = str((n_meta or {}).get("result_variable") or "").strip()
                if result_variable:
                    new_vars[result_variable] = result_data
                return {
                    "last_node": n_id,
                    "output": f"HTTP request executed successfully. Status: {response.status_code}",
                    "variables": new_vars,
                    "total_tokens": 0,
                    "history": [{"node": n_id, "output": "HTTP request executed", "tokens_used": 0}]
                }
            else:
                new_vars = {
                    **variables,
                    "http_success": False,
                    "http_error": response.error_message,
                    "http_status_code": response.status_code
                }
                return {
                    "last_node": n_id,
                    "output": f"HTTP request error: {response.error_message}",
                    "variables": new_vars,
                    "error": response.error_message,
                    "total_tokens": 0,
                    "history": [{"node": n_id, "output": "HTTP request error", "tokens_used": 0}]
                }

        except grpc.RpcError as e:
            logger.error(f"Node {n_id}: gRPC Communication Error - {e.code()}: {e.details()}")
            new_vars = {**state.get("variables", {}), "http_success": False, "http_error": f"gRPC Error: {e.code()}"}
            return {
                "last_node": n_id,
                "output": f"System Error: Could not reach HTTP Request Service ({e.code()}).",
                "variables": new_vars,
                "error": str(e.details())
            }
        except Exception as e:
            logger.error(f"Node {n_id}: Unexpected HTTP request error - {e}", exc_info=True)
            return {"last_node": n_id, "output": "Unexpected error during HTTP request execution.", "error": str(e)}

    async def _process_document(self, n_id: str, n_meta: Dict, state: GraphState,
                                llm=None,
                                credentials: Optional[LLMCredentialSet] = None,
                                require_user_key: bool = False,
                                caller_kind: str = "") -> Dict:
        """Fetch a caller-attached document and extract prompt-ready text from it.

        The document itself NEVER enters GraphState: it would be checkpointed to Postgres on
        every step and rendered wholesale into the next node's prompt by
        ``format_state_context``. Only the extracted text (capped) and a small metadata dict
        are written, under ``variables`` so the existing reducer merges them into whatever
        the graph already holds.

        The reference arrives per run - a graph definition is persisted and shared, so it
        cannot live in node metadata - under ``context[document_ref_key]``.

        SLOTS: a node may also name where its text lands (``output_variable`` /
        ``meta_variable``) and how it is titled (``label``), all with the historical defaults.
        That is what makes several documents possible in one run: a graph gives each of its
        DOCUMENT nodes its own key and its own variable, so the second extraction lands beside
        the first instead of overwriting it, and the ACTION node downstream can name both.
        See :func:`app.graph_engine.graph_utils.document_variables_of`.

        LLM MODE: with ``normalize: "llm"`` the node first reads the sheet grid and asks the
        model for ONE mapping per sheet (see :meth:`_propose_mappings`), then parses with
        those mappings in hand. The model never sees the whole file and never writes a cell -
        the parser applies what survived validation, or falls back to the deterministic
        rules when nothing did.
        """
        context = state.get("context") or {}
        if not isinstance(context, dict):
            context = {}
        ref_key, output_variable, meta_variable, label = document_variables_of(n_meta)
        document_ref = str(context.get(ref_key) or "").strip()

        if not document_ref:
            return {
                "last_node": n_id,
                "output": f"Document node found no '{ref_key}' in the run context.",
                "error": f"missing_context:{ref_key}",
            }

        if self.document_fetcher is None:
            logger.error("Node %s: no document fetcher is configured", n_id)
            return {
                "last_node": n_id,
                "output": "Error: Document Service is not connected.",
                "error": "Document Service Unavailable",
            }

        try:
            content, filename, _mime_type = await self.document_fetcher(document_ref)
        except KeyError:
            # The Java callback raises KeyError for NOT_FOUND: either the reference was never
            # uploaded, or it is past its retention deadline. A readable node error either
            # way - a raw gRPC status would be noise in the chat.
            logger.warning("Node %s: document %r is not available", n_id, document_ref)
            return {
                "last_node": n_id,
                "output": "Error: that document is no longer available "
                          "(it may never have been uploaded, or its retention period has elapsed).",
                "error": f"document_not_found:{document_ref}",
            }
        except grpc.RpcError as e:
            logger.error("Node %s: document fetch failed - %s: %s", n_id, e.code(), e.details())
            return {
                "last_node": n_id,
                "output": f"System Error: could not fetch the document ({e.code()}).",
                "error": str(e.details()),
            }
        except Exception as e:
            logger.error("Node %s: document fetch raised - %s", n_id, e, exc_info=True)
            return {
                "last_node": n_id,
                "output": f"Error: could not fetch the document ({e}).",
                "error": str(e),
            }

        limits = DocumentLimits.from_metadata(n_meta)
        normalize = NormalizeOptions.from_metadata(n_meta)
        try:
            parsed = parse_document(content, limits, filename=filename, normalize=normalize)
        except DocumentParseError as e:
            logger.error("Node %s: could not parse document: %s", n_id, e)
            return {
                "last_node": n_id,
                "output": f"Error: {e}",
                "error": str(e),
            }

        stats = dict(parsed.stats)
        meta = {
            "document_ref": document_ref,
            "filename": filename,
            "kind": parsed.kind,
            "truncated": parsed.truncated,
            "chars": len(parsed.text),
            "stats": stats,
        }
        if label:
            meta["label"] = label
        if parsed.warnings:
            meta["warnings"] = parsed.warnings

        # A titled slot labels its own text. Two files named timesheets_text / report_text are
        # already distinguishable by variable name, but a graph may keep both at the default or
        # call them anything at all - so when the node declares a label, the text the model
        # reads opens with it. Only the extracted text is prefixed; `chars` stays the parsed
        # length, so the caps report what the parser did and not what the header added.
        text = f"# {label}\n\n{parsed.text}" if label else parsed.text

        summary = (
            f"Parsed {parsed.kind} '{filename}'"
            + (f" as '{label}'" if label else "")
            + f": {len(parsed.text)} chars"
            + (f" (truncated: {'; '.join(parsed.warnings)})" if parsed.warnings else "")
        )
        return {
            "last_node": n_id,
            "output": summary,
            "variables": {output_variable: text, meta_variable: meta},
            "total_tokens": 0,
            "history": [{"node": n_id, "output": summary, "tokens_used": 0}],
            "llm_billing": "",
        }

