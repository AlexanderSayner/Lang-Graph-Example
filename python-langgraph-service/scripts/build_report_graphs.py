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
    "1. http_result - JSON-массив участников проекта из API трекера; у каждого есть id (число),\n"
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
    "Ответ состоит из ДВУХ частей.\n"
    "Часть 1 - одна короткая строка для человека, без фигурных скобок:\n"
    "✅ Проект «<project_name>» (id <project_id>): запрошу отчёт у <participants_count> участника(ов);\n"
    "не найдены в Orchestra (<dropped_count>): <dropped_names>\n"
    "(если dropped нет - вместо второй половины напиши «все участники адресуемы»).\n"
    "Часть 2 - с новой строки строго один JSON-объект в блоке ```json, без пояснений после него:\n"
    '{"project_id": 1247, "project_name": "Orkestra",\n'
    ' "participants_count": 2,\n'
    ' "participants": [{"tracker_id": 3693, "name": "Александр Ежаков",\n'
    '                   "subscriber_id": "6ae1d8af0466ce552b44ba89", "telegram_id": "705196288"}],\n'
    ' "dropped_count": 10,\n'
    ' "dropped_names": "Дамир Батыров, Татьяна Бабич",\n'
    ' "dropped": [{"tracker_id": 3981, "name": "Дамир Батыров"}]}\n'
    "\n"
    "participants_count = число элементов participants; dropped_count = число dropped;\n"
    "dropped_names = имена всех dropped через запятую (пустая строка, если их нет).\n"
    "Ни одного лишнего ключа. Числа - числами, без кавычек."
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



# --- A: project picker + members + subscriber join ---------------------------------------------

def build_picker():
    nodes, edges = source_view(PICKER_SOURCE)
    by_id = {n["nodeId"]: n for n in nodes}
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
        edge("pk_gate", asked["nodeId"], "has_query == 0"),
        edge(search["nodeId"], "pk_project_id", "output == 0"),
        edge("pk_project_id", "pk_project_name"),
        edge("pk_project_name", "pk_users"),
        edge("pk_users", "pk_match"),
        edge("pk_match", end["nodeId"]),
    ]
    build("report-project-picker", "Отчёты: выбор проекта и участников", nodes, edges,
          {"llm_provider": "deepseek"})
    print("  prod ids - start:", start["nodeId"], "end:", end["nodeId"],
          "search:", search["nodeId"], "clarify:", clarify["nodeId"],
          "| nodes", len(nodes), "edges", len(edges))


# --- B: per-participant comparison --------------------------------------------------------------

def build_compare():
    nodes, edges = source_view(COMPARE_SOURCE)
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
    print(f"  timesheet URLs parametrised: {patched}")
    build("report-participant-compare", "Отчёты: сверка отчёта участника", nodes, edges,
          {"llm_provider": "deepseek"})
    humans = [n["nodeId"] for n in nodes if n["nodeType"] == "HUMAN"]
    docs = [n["nodeId"] for n in nodes if n["nodeType"] == "DOCUMENT"]
    print("  humans:", humans, "documents:", docs)


if __name__ == "__main__":
    build_picker()
    build_compare()
    listed = gql("query { listGraphs(pageSize: 50, pageToken: \"\") { graphs { graphId graphName "
                 "status nodeCount } } }")
    data = (listed.get("data") or {}).get("listGraphs") or {}
    for g in data.get("graphs") or []:
        if str(g.get("graphId", "")).startswith("report-"):
            print("  ", json.dumps(g, ensure_ascii=False))
    if not data.get("graphs"):
        print("  listGraphs:", json.dumps(listed, ensure_ascii=False)[:400])
