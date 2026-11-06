#!/usr/bin/env python3
"""Build the two REAL graphs of the report-collection process on a LangGraph service.

Both graphs start from tuned production definitions, read with ``getGraphView`` from a source
service, so every prompt fitted by hand comes along verbatim; only what the new process needs is
added on top:

A  ``report-project-picker``  (source ``graph-1793864456734``, "Трекер: выбор проекта")
   * ``result_variable`` on the login call - its token used to be overwritten by the very next
     request, which only worked while nothing after it needed the token;
   * the chosen project's id and name as plain variables - a URL template and a prompt both read
     them, and an HTTP node templates out of ``variables``, never out of ``context``;
   * an ACTION gate in front of the question node, so a run whose first message already names the
     project goes to the tracker on that same turn instead of asking for it a second time;
   * an ACTION asking for the name in ONE human line when the message has none: the pause the gate
     falls into is entered BEFORE the HUMAN node runs, so the question has to come from a node that
     actually speaks - otherwise the gate's own JSON becomes the "question" the chat is shown;
   * HTTP ``GET /api/v1/project/{projectId}/users`` - the members the prod graph never fetched;
   * an ACTION joining those members with the ``subscribers`` snapshot Orchestra hands the run
     (``sendSubscribers: true`` on the node) and answering ONE JSON: project, participants
     (addressable through Telegram), dropped (everyone else, named in the message);
   * an END without a prompt, so the match node's JSON stays the run's answer.

B  ``report-participant-compare``  (source ``imported-graph-1793853729074``, the comparison graph)
   * the timesheet URL parametrised - ``?userId={{user_id}}&dateBegin={{date_begin}}&dateEnd=
     {{date_end}}`` - the three values arrive from Orchestra's node ``turnVariables``.

Env (all optional):
  LG_GRAPHQL_URL          where to build            - default http://localhost:9191/graphql
  LG_SOURCE_GRAPHQL_URL   where to read the graphs  - default https://agent-graph-flow.ru/graphql
  LG_SOURCE_USER / LG_SOURCE_PASSWORD               - default Alex / secret
  TRACKER_URL             tracker base              - default https://nordclan.dev:8888

Run:  python scripts/build_report_graphs.py        (stdlib only)
"""
import json
import os
import ssl
import urllib.request

TARGET = os.environ.get("LG_GRAPHQL_URL", "http://localhost:9191/graphql")
SOURCE = os.environ.get("LG_SOURCE_GRAPHQL_URL", "https://agent-graph-flow.ru/graphql")
SOURCE_USER = os.environ.get("LG_SOURCE_USER", "Alex")
SOURCE_PASSWORD = os.environ.get("LG_SOURCE_PASSWORD", "secret")
TRACKER = os.environ.get("TRACKER_URL", "https://nordclan.dev:8888")

PICKER_SOURCE = "graph-1793864456734"
COMPARE_SOURCE = "imported-graph-1793853729074"

VIEW_QUERY = """query($g:String!){ getGraphView(graphId:$g){ graphId graphName status
  nodes{ nodeId nodeType handlerName metadata position{x y} }
  edges{ source target condition } } }"""


def post(url, query, variables=None, cookie=None):
    """One GraphQL POST. Returns (parsed body, session cookie or the one passed in).

    The builder is session-authenticated: a browser logs in and carries a cookie, and this does the
    same two steps. Certificate checks are off on purpose - these deployments are reached by name
    over a self-managed TLS, and a script that fetches tuned prompts is not where a hostname
    mismatch should stop a rollout.
    """
    payload = {"query": query}
    if variables:
        payload["variables"] = variables
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 **({"Cookie": cookie} if cookie else {})},
        method="POST",
    )
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with urllib.request.urlopen(req, timeout=120, context=ctx) as resp:
        body = json.loads(resp.read().decode())
        fresh = ";".join(c.split(";", 1)[0] for c in (resp.headers.get_all("Set-Cookie") or [])
                         if "SESSION" in c or "JSESSIONID" in c)
    return body, (f"{cookie};{fresh}" if cookie and fresh else (fresh or cookie))


def gql(query, variables=None):
    """A call against the build target."""
    return post(TARGET, query, variables)[0]


def source_view(graph_id):
    """The tuned definition of `graph_id` as (nodes, edges) build-inputs, read from SOURCE.

    The builder is session-first: log in, then carry the session cookie - the two steps the builder
    page itself goes through.
    """
    _, cookie = post(SOURCE, "mutation($u:String!,$p:String!){login(username:$u,password:$p)"
                            "{success message username}}", {"u": SOURCE_USER, "p": SOURCE_PASSWORD})
    body, _ = post(SOURCE, VIEW_QUERY, {"g": graph_id}, cookie=cookie)
    view = (body.get("data") or {}).get("getGraphView")
    if not view or not view.get("nodes"):
        raise SystemExit(f"source {graph_id} not read: {json.dumps(body, ensure_ascii=False)[:400]}")
    nodes = [{"nodeId": n["nodeId"], "nodeType": n["nodeType"],
              "handlerName": n.get("handlerName") or f"{n['nodeId']}Handler",
              "metadata": n.get("metadata") or {},
              "position": {"x": float((n.get("position") or {}).get("x", 0)),
                           "y": float((n.get("position") or {}).get("y", 0))}}
             for n in view["nodes"]]
    edges = [{"source": e["source"], "target": e["target"], "condition": e.get("condition")}
             for e in view["edges"]]
    return nodes, edges


def node(node_id, node_type, metadata, x=0, y=0):
    return {"nodeId": node_id, "nodeType": node_type, "handlerName": f"{node_id}Handler",
            "metadata": metadata, "position": {"x": float(x), "y": float(y)}}


def edge(source, target, condition=None):
    return {"source": source, "target": target, "condition": condition}


def build(graph_id, graph_name, nodes, edges, config):
    result = gql(
        "mutation($input: BuildGraphInput!) { buildGraph(input: $input) "
        "{ success graphId message } }",
        {"input": {"graphId": graph_id, "graphName": graph_name, "nodes": nodes,
                   "edges": edges, "config": config}},
    )
    print(f"{graph_id}: {json.dumps(result, ensure_ascii=False)[:400]}")


AUTH_HEADERS = ('{\n  "Authorization" : "Basic {{login_result.token}}",'
                '\n  "Content-Type" : "application/json"\n}')
MATCH_PROMPT = (
    "Данные для сопоставления.\n"
    "1. users_result - JSON-массив участников проекта из API трекера; у каждого есть id (число),\n"
    "   fullNameRu и login. Это все люди проекта.\n"
    "2. В секции STATIC CONTEXT переменная subscribers - JSON-массив людей, которых система умеет\n"
    "   писать в Telegram: subscriberId, name, trackerId, telegramChatId, telegramUsername.\n"
    "3. project_id и project_name - выбранный проект.\n"
    "\n"
    "Сопоставь участника трекера с подписчиком СТРОГО по числу: participant.id == subscribers.trackerId.\n"
    "Никакого сопоставления по именам и логинам: имена расходятся, числа нет.\n"
    "Подписчик без telegramChatId не адресуем - такой участник идёт в dropped.\n"
    "Участник, для которого подписчика нет, тоже идёт в dropped.\n"
    "\n"
    "Ответ - СТРОГО один JSON-объект, и больше ничего: ни строки «для человека», ни пояснений до\n"
    "или после него, ни markdown-обёртки. Человек этот объект не читает - процесс сам пишет\n"
    "сообщение из его полей, а объект нужен движку процесса.\n"
    '{"project_id": 1247, "project_name": "Orkestra",\n'
    ' "participants_count": 2,\n'
    ' "participants": [{"tracker_id": 3693, "name": "Александр Ежаков",\n'
    '                   "subscriber_id": "6ae1d8af0466ce552b44ba89", "telegram_id": "705196288"}],\n'
    ' "dropped_count": 10,\n'
    ' "dropped_names": "Дамир Батыров, Татьяна Бабич",\n'
    ' "dropped_table": "3981 - Дамир Батыров\\n3982 - Татьяна Бабич",\n'
    ' "dropped": [{"tracker_id": 3981, "name": "Дамир Батыров"}]}\n'
    "\n"
    "participants_count = число элементов participants; dropped_count = число dropped;\n"
    "dropped_names = имена всех dropped через запятую (пустая строка, если их нет);\n"
    "dropped_table = по одной строке на каждого dropped вида «<tracker_id> - <ФИО>», разделитель -\n"
    "перевод строки; если dropped нет - напиши в dropped_table слово «нет». Ни одного лишнего ключа.\n"
    "Числа - числами, без кавычек."
)

GATE_PROMPT = (
    "Задача - определить, известно ли уже название проекта, и ничего больше.\n"
    "input - сообщение инициатора процесса. query - название проекта, которое процесс прислал из "
    "своего контекста.\n"
    "Название считается известным, если в input есть слово, похожее на имя проекта (любое, кроме "
    "коротких подтверждений: да, ок, хорошо, подтвердить, старт, start, нет, ?).\n"
    "Верни СТРОГО один JSON-объект и ничего больше:\n"
    '{"has_query": 1} - название есть в input ИЛИ в query;\n'
    '{"has_query": 0} - названия нет ни там, ни там.'
)

# The gate's `has_query == 0` branch used to fall straight into the pause, and the pause is entered
# BEFORE the HUMAN node runs - so nothing had asked the question yet, and the last thing the run
# said was the gate's own JSON (every subscriber of the process, in the chat). This node speaks
# first: one human line, nothing else, and only `query` in its context - never the subscriber list.
ASK_PROMPT = (
    "Сообщение инициатора не содержит названия проекта. Спроси у него название проекта ОДНОЙ "
    "короткой строкой - например: «Какой проект? Нужно название из трекера.» "
    "Ничего не перечисляй: ни JSON, ни списков, ни пояснений, ни вариантов."
)



# --- A: project picker + members + subscriber join ---------------------------------------------

def wire_picker(nodes, edges):
    """The picker's own nodes and edges, applied on top of the prod view it starts from.

    Split out of ``build_picker`` so the wiring can be asserted without a network call: which node
    the gate's "no name yet" branch reaches, and that the pause still sits behind a node that
    actually speaks. Mutates both lists and returns ``(nodes, edges, ids)``.
    """
    start = next(n for n in nodes if n["nodeType"] == "START")
    end = next(n for n in nodes if n["nodeType"] == "END")
    search = next(n for n in nodes
                  if n["nodeType"] == "ACTION" and "Найди проект" in n["metadata"].get("system_prompt", ""))
    clarify = next(n for n in nodes if (n["metadata"].get("label") or "").strip() == "Уточнение")
    login = next(n for n in nodes if n["nodeType"] == "HTTP_REQUEST"
                 and "/auth/login" in n["metadata"].get("url", ""))
    # The prod question node: the HUMAN the START node points at.
    asked = next(n for n in nodes if n["nodeType"] == "HUMAN"
                 and any(e["source"] == start["nodeId"] and e["target"] == n["nodeId"] for e in edges))

    # Turn 1 of a real process already carries the project name (the trigger message), so START
    # must not demand it a second time: it confirms what it was given, and the gate below decides
    # whether to go straight to the tracker or to ask.
    start["metadata"]["system_prompt"] = (
        "Одно короткое сообщение: скажи, какой проект берём. Название возьми из input (сообщение "
        "иниатора), если его там нет - из переменной query. Если названия нет ни там ни там - "
        "спроси: «Название проекта?» Ничего не выдумывай, не объясняй и не добавляй других строк."
    )
    search["metadata"]["system_prompt"] = (
        search["metadata"]["system_prompt"]
        + "\nЕсли в input нет названия проекта (там короткое подтверждение вроде «да», «ок» или "
          "пусто), ищи проект по переменной query."
    )
    # Prod ended by having the END node print the project; the JSON answer of the new last node
    # has to reach the run output untouched, so END goes back to a pass-through.
    end["metadata"]["system_prompt"] = ""
    # The token has to outlive the project list: prod templated {{http_result.token}}, which the
    # very next call overwrote - it only worked there because nothing followed.
    for n in nodes:
        if n["nodeType"] != "HTTP_REQUEST":
            continue
        url = n["metadata"].get("url", "")
        if "/auth/login" in url:
            n["metadata"]["result_variable"] = "login_result"
        elif "/api/v1/project?" in url:
            n["metadata"]["headers"] = AUTH_HEADERS

    nodes += [
        node("pk_gate", "ACTION", {
            "label": "название уже известно?", "context_mode": "minimal",
            "llm_max_tokens": "80", "llm_temperature": "0",
            "system_prompt": GATE_PROMPT,
        }, 690, 380),
        # Speaks before the pause: the question the chat is shown comes from here, not from the
        # gate's JSON. Only `query` is in its context, so the subscriber list never reaches a
        # prompt (or a message) on this branch.
        node("pk_ask", "ACTION", {
            "label": "какой проект?", "context_mode": "minimal",
            "include_variables": "query",
            "llm_max_tokens": "80", "llm_temperature": "0",
            "system_prompt": ASK_PROMPT,
        }, 470, 380),
        node("pk_project_id", "ACTION", {
            "label": "id проекта", "context_mode": "slim",
            "output_variable": "project_id", "llm_max_tokens": "60", "llm_temperature": "0",
            "system_prompt": (
                "В http_result - список проектов (поле data: id и название), в input - запрос "
                "пользователя. Верни ТОЛЬКО числовой id единственного подходящего проекта: одни "
                "цифры, без кавычек, без слова id, без пояснений, без markdown."
            ),
        }, 690, 470),
        node("pk_project_name", "ACTION", {
            "label": "название проекта", "context_mode": "slim",
            "output_variable": "project_name", "llm_max_tokens": "120", "llm_temperature": "0",
            "system_prompt": (
                "В http_result - список проектов, в input - запрос пользователя. Верни ТОЛЬКО точное "
                "название того единственного подходящего проекта - одной строкой, ровно как оно "
                "записано в списке, без кавычек и без пояснений."
            ),
        }, 690, 560),
        node("pk_users", "HTTP_REQUEST", {
            "label": "участники проекта", "method": "GET",
            "url": TRACKER + "/api/v1/project/{{project_id}}/users",
            "body": "", "headers": AUTH_HEADERS, "result_variable": "users_result",
            "system_prompt": "",
        }, 690, 650),
        node("pk_match", "ACTION", {
            "label": "участники + подписчики", "context_mode": "slim",
            "llm_max_tokens": "3000", "llm_temperature": "0",
            "system_prompt": MATCH_PROMPT,
        }, 690, 740),
    ]

    # search -> END (output == 0) becomes search -> the member chain; the "ask again" branches stay.
    edges = [e for e in edges if not (e["source"] == search["nodeId"] and e["target"] == end["nodeId"])]
    # START no longer falls into the question: the gate decides. An unknown name still asks,
    # a known one goes straight to the tracker on the same turn.
    edges = [e for e in edges if not (e["source"] == start["nodeId"] and e["target"] == asked["nodeId"])]
    edges += [
        edge(start["nodeId"], "pk_gate"),
        edge("pk_gate", login["nodeId"], "has_query == 1"),
        # No name in the message: ask for it in one human line, THEN pause for the answer. The
        # pause is entered before the HUMAN node runs, so without this step the last thing the run
        # said was the gate's own JSON - and that is what the chat was shown as the question.
        edge("pk_gate", "pk_ask", "has_query == 0"),
        edge("pk_ask", asked["nodeId"]),
        edge(search["nodeId"], "pk_project_id", "output == 0"),
        edge("pk_project_id", "pk_project_name"),
        edge("pk_project_name", "pk_users"),
        edge("pk_users", "pk_match"),
        edge("pk_match", end["nodeId"]),
    ]
    ids = {"start": start["nodeId"], "end": end["nodeId"], "search": search["nodeId"],
           "asked": asked["nodeId"], "login": login["nodeId"], "clarify": clarify["nodeId"]}
    return nodes, edges, ids


def build_picker():
    nodes, edges, ids = wire_picker(*source_view(PICKER_SOURCE))
    build("report-project-picker", "Отчёты: выбор проекта и участников", nodes, edges,
          {"llm_provider": "deepseek"})
    print("  prod ids - start:", ids["start"], "end:", ids["end"],
          "search:", ids["search"], "clarify:", ids["clarify"],
          "| nodes", len(nodes), "edges", len(edges))


# --- B: per-participant comparison --------------------------------------------------------------

def patch_compare_urls(nodes):
    """The parametrised timesheet URL and the saved login token - shared by every build of
    this graph: they are fixes to the source definition, not to any one version of it."""
    patched = 0
    for n in nodes:
        url = n["metadata"].get("url", "")
        if "/timesheet" in url:
            n["metadata"]["url"] = (TRACKER + "/api/v1/timesheet?userId={{user_id}}"
                                    "&dateBegin={{date_begin}}&dateEnd={{date_end}}")
            n["metadata"]["label"] = "таймшиты участника"
            patched += 1
        elif "/auth/login" in url:
            n["metadata"]["result_variable"] = "login_result"
    return patched


def build_compare():
    nodes, edges = source_view(COMPARE_SOURCE)
    patched = patch_compare_urls(nodes)
    print(f"  timesheet URLs parametrised: {patched}")
    build("report-participant-compare", "Отчёты: сверка отчёта участника", nodes, edges,
          {"llm_provider": "deepseek"})
    humans = [n["nodeId"] for n in nodes if n["nodeType"] == "HUMAN"]
    docs = [n["nodeId"] for n in nodes if n["nodeType"] == "DOCUMENT"]
    print("  humans:", humans, "documents:", docs)


# --- B2: the same comparison, with the uploaded file normalized before the model reads it ----
# The v1 graph handed the model the export AS IT COMES OUT OF EXCEL: the title row read as
# the table's header, merged person cells as blank names, and the totals/pivot blocks below
# the data as if they were working days. On a real company-wide export that produced 61
# invented discrepancies and a message too long for Telegram. v2 lets the DOCUMENT node
# normalize the sheet first (node metadata "normalize": "table") and rewrites the two parse
# prompts around the shape that comes out.

TIMESHEET_PARSE_PROMPT_V2 = (
    "Ниже — ответ API трекера (переменная http_result): список таймшитов одного пользователя. "
    "Преврати его в JSON-массив записей.\n\n"
    "Каждый элемент — объект с РОВНО такими ключами:\n"
    '{"person": "фамилия", "date": "YYYY-MM-DD", "task": "ключ и название", "hours": 4.5}\n\n'
    "ПРАВИЛА:\n"
    "- person — фамилия участника из переменной participant_name («Александр Ежаков» → «Ежаков»): "
    "бери слово-фамилию — оканчивается на -ов/-ев/-ин/-ский/-цкий и их женские формы "
    "-ова/-ева/-ина/-ская/-цкая, а также -ко; если слово одно — оно и есть фамилия. "
    "Если participant_name в контексте нет — верни пустой массив [] (сравнивать не с чем);\n"
    "- date — поле onDate, формат уже YYYY-MM-DD;\n"
    '- task — "{project.prefix}-{task.id} {task.name}", например '
    "«ORK-104256 Поддержка human in the loop»;\n"
    '- hours — поле spentTime, число ("8.00" → 8.0).\n'
    "Если у даты несколько записей — каждая отдельным элементом массива. Ничего не выдумывай: "
    "только то, что есть в ответе.\n"
    "Выведи ТОЛЬКО JSON-массив, каждый объект на отдельной строке, без markdown-обёртки и без "
    "текста до и после.\n\n"
    "Если в http_result нет списка записей (например, ошибка HTTP), верни пустой массив []."
)

FILE_PARSE_PROMPT_V2 = (
    "Прочитай переменную file_text — загруженный пользователем отчёт. Преврати его в JSON-массив "
    "записей.\n\n"
    "Каждый элемент — объект с РОВНО такими ключами:\n"
    '{"person": "фамилия", "date": "YYYY-MM-DD", "task": "ключ и название", "hours": 4.5}\n\n'
    "ВИД 1 — НОРМАЛИЗОВАННАЯ ТАБЛИЦА (первый рядок таблицы: ФИО | Дата | Ключ | Задача | Часы):\n"
    "- person — фамилия ОДНИМ словом из колонки ФИО (слово оканчивается на -ов/-ев/-ин/-ский/"
    "-цкий и их женские формы -ова/-ева/-ина/-ская/-цкая, а также -ко; если слов одно — оно и "
    "есть фамилия);\n"
    "- date — как есть: колонка Дата уже в формате YYYY-MM-DD;\n"
    "- task — «{Ключ} {Задача}» если Ключ не пустой, иначе только Задача; на выходе должен "
    "получиться «KEY-123 Название», ровно в таком виде, как в отчёте трекера;\n"
    "- hours — число из колонки Часы; дробные не округляй (4.5 остаётся 4.5).\n"
    "Такая таблица уже очищена: заголовки, итоги и сводные блоки в ней отсутствуют. НЕ "
    "фильтруй строки по сотруднику и не отбрасывай «лишние»: включай ВСЕ строки подряд — файл "
    "может быть отчётом на всю компанию сразу.\n\n"
    "ВИД 2 — ИСХОДНАЯ ТАБЛИЦА (нормализация не сработала: первая строка — заголовок вида "
    "«Отчет по компании» или вообще без названия, колонки вроде «Дата приема на работу», "
    "«Ключ задачи», «Billable»):\n"
    "- настоящую шапку (ФИО, Дата, Задача, …) ищи среди первых 25 строк и считай таблицу от неё;\n"
    "- person — фамилия ОДНИМ словом из ячейки ФИО (то же правило, что выше);\n"
    "- date — YYYY-MM-DD, в какой бы записи ни был оригинал (ДД.ММ.ГГГГ, ДД.ММ, YYYY-MM-DD);\n"
    "- task — ключ или номер задачи, если он есть (любой формат: INTGR-4914, INV-64371, TB-104268, "
    "T-123) и название через пробел; ключа нет — просто название;\n"
    "- hours — число из колонки затраченных часов; текстовое время («4h 30m», «4 ч 30 мин») "
    "переведи в часы (4.5), не вставляй текст и не округляй.\n"
    "ИГНОРИРУЙ: строки заголовков и пустые; итоговые («Всего billable:», «Всего часов:»); "
    "сводные блоки, где в колонке даты стоит название раздела («Показатели», "
    "«*Направление QA», «MLSense(Ульяновск)») — это не рабочие дни.\n\n"
    "ОБЩЕЕ:\n"
    "- каждая строка задачи попадает в массив ровно один раз, без пропусков и дублей; дойди до "
    "самого конца текста;\n"
    "- ПЕРЕД ОТВЕТОМ ПЕРЕСЧИТАЙ: сколько строк задач в исходнике — ровно столько объектов; "
    "если не сошлось — допиши недостающие.\n\n"
    "Верни ТОЛЬКО JSON-массив, каждый объект на отдельной строке, без markdown-обёртки и без "
    "текста до и после."
)

REPORT_LENGTH_RULE = (
    "ДЛИНА ОТВЕТА: сообщение уходит в Telegram одним сообщением с жёстким пределом 4096 "
    "символов, поэтому отвечай не длиннее 3400 символов. Если в какой-то секции больше 12 "
    "пунктов — не перечисляй все: оставь первые 12 и допиши строкой вида «… и ещё K расхождений "
    "(даты: ДД.ММ, ДД.ММ)», где K — число НЕ перечисленных пунктов этой секции. Заголовок секции "
    "всё равно называет общее число находок N. Правило 1:1 из шага 4 действует для "
    "перечисленных пунктов; сокращение помечается строкой «… и ещё», и вместе с ней строки "
    "покрывают находки секции без выдуманных строк."
)



def build_compare_v2():
    """Copy of ``report-participant-compare`` with normalization + bounded output.

    Same source graph and the same URL fixes as v1 - the point is that v1 stays deployable
    and untouched while the new behaviour is exercised by the flow that opts in.
    """
    nodes, edges = source_view(COMPARE_SOURCE)
    print(f"  timesheet URLs parametrised: {patch_compare_urls(nodes)}")

    document = next(n for n in nodes if n["nodeType"] == "DOCUMENT")
    timesheet = next(n for n in nodes if n["nodeType"] == "ACTION"
                     and n["metadata"].get("output_variable") == "doc1")
    file_parse = next(n for n in nodes if n["nodeType"] == "ACTION"
                      and n["metadata"].get("output_variable") == "doc2")
    report = next(n for n in nodes if n["nodeType"] == "ACTION"
                  and "массив matched" in n["metadata"].get("system_prompt", ""))

    # The sheet arrives pre-shaped: header found under the title rows, merged person cells
    # filled down, totals and pivots dropped, task key on its own, five narrow columns.
    document["metadata"]["normalize"] = "table"
    # The timesheet side now names its person from the run instead of a hardcoded one.
    timesheet["metadata"]["include_variables"] = "http_result,participant_name"
    timesheet["metadata"]["system_prompt"] = TIMESHEET_PARSE_PROMPT_V2
    file_parse["metadata"]["system_prompt"] = FILE_PARSE_PROMPT_V2
    report["metadata"]["system_prompt"] = (
        report["metadata"].get("system_prompt", "").rstrip() + "\n\n" + REPORT_LENGTH_RULE)

    build("report-participant-compare-v2", "Отчёты: сверка отчёта участника v2", nodes, edges,
          {"llm_provider": "deepseek"})
    print("  normalize:", document["nodeId"], "| doc1:", timesheet["nodeId"],
          "| doc2:", file_parse["nodeId"], "| report:", report["nodeId"])



if __name__ == "__main__":
    build_picker()
    build_compare()
    build_compare_v2()
    listed = gql("query { listGraphs(pageSize: 50, pageToken: \"\") { graphs { graphId graphName "
                 "status nodeCount } } }")
    data = (listed.get("data") or {}).get("listGraphs") or {}
    for g in data.get("graphs") or []:
        if str(g.get("graphId", "")).startswith("report-"):
            print("  ", json.dumps(g, ensure_ascii=False))
    if not data.get("graphs"):
        print("  listGraphs:", json.dumps(listed, ensure_ascii=False)[:400])
