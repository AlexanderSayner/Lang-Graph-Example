# Request flow

> B2B (backend-to-backend) callers use the `external.v1` gRPC facade instead of these HTTP
> requests — authentication headers, thread scoping and running a graph on your own LLM keys
> are covered in [B2B_INTEGRATION.md](B2B_INTEGRATION.md).

#### Build graph 
```http request
POST http://localhost:9191/graphql

{
    "query": "mutation BuildGraph($input: BuildGraphInput!) { buildGraph(input: $input) { success graphId message } }",
    "variables": {
        "input": {
            "graphId": "graph-001",
            "graphName": "Test Graph",
            "nodes": [
                { "nodeId": "node-1", "nodeType": "START", "handlerName": "startHandler", "position": { "x": 0.0, "y": 0.0 } },
                { "nodeId": "node-2", "nodeType": "END", "handlerName": "endHandler", "position": { "x": 0.0, "y": 160.0 } }
            ],
            "edges": [
                { "source": "node-1", "target": "node-2" }
            ]
        }
    }
}
```

> `NodeInput.position` (`{ x, y }`) is **optional**. When present, `buildGraph` persists it into the
> `coordinates` column in the same row write as the definition, so saving from the graph builder needs
> no second call. Omit it and the graph simply opens with an empty layout, exactly as before.
> Layout-only changes (drag / auto layout without rebuilding) use the dedicated `saveGraphCoordinates`
> mutation instead, which still takes the flat `positions: [{ nodeId, x, y }]` pair.

#### Simple user case
```http request
POST http://localhost:9191/graphql

{
  "query": "mutation BuildGraph($input: BuildGraphInput!) { buildGraph(input: $input) { success graphId message } }",
  "variables": {
    "input": {
      "graphId": "graph-003",
      "graphName": "Smart Support Triage",
      "nodes": [
        {
          "nodeId": "triage",
          "nodeType": "START",
          "handlerName": "triageHandler",
          "metadata": {
            "system_prompt": "You are a router. Analyze the user input and determine the intent."
          }
        },
        {
          "nodeId": "technical",
          "nodeType": "ACTION",
          "handlerName": "technicalHandler",
          "metadata": {
            "system_prompt": "You are a Senior Technical Support Engineer. Provide a concise solution to the technical problem."
          }
        },
        {
          "nodeId": "billing",
          "nodeType": "ACTION",
          "handlerName": "billingHandler",
          "metadata": {
            "system_prompt": "You are a Billing Specialist. Help the user with their payment or invoice issues politely."
          }
        },
        {
          "nodeId": "general",
          "nodeType": "ACTION",
          "handlerName": "generalHandler",
          "metadata": {
            "system_prompt": "You are a General Assistant. Answer general questions briefly."
          }
        },
        {
          "nodeId": "resolve",
          "nodeType": "END",
          "handlerName": "resolveHandler",
          "metadata": {
            "system_prompt": "You are a closing assistant. Summarize the provided solution in one sentence and wish the user a good day."
          }
        }
      ],
      "edges": [
        { "source": "triage", "target": "technical", "condition": "ticket_type == 'technical'" },
        { "source": "triage", "target": "billing", "condition": "ticket_type == 'billing'" },
        { "source": "triage", "target": "general", "condition": "ticket_type == 'general'" },
        { "source": "technical", "target": "resolve" },
        { "source": "billing", "target": "resolve" },
        { "source": "general", "target": "resolve" }
      ]
    }
  }
}
```

#### Human-in-loop example
```graphql

mutation BuildGraph {
    buildGraph(
        input: {
            graphId: "graph-005"
            graphName: "Smart Billing Flow"
            nodes: [
                { nodeId: "triage", nodeType: "START", handlerName: "triageHandler" }
                {
                    nodeId: "billing"
                    nodeType: "ACTION"
                    handlerName: "billingHandler"
                    metadata: {
                        system_prompt: "You are a billing assistant. Ask the user for their account number to proceed."
                    }
                }
                {
                    nodeId: "verify_billing"
                    nodeType: "HUMAN"
                    handlerName: "waitForUser"
                    metadata: {
                        system_prompt: "You are a strict validator. Analyze the user input. If it looks like an account number (e.g., starts with ACC), respond exactly with {\"valid\": \"true\"}. Otherwise, respond exactly with {\"valid\": \"false\"}. DO NOT use any other words."
                    }
                }
                {
                    nodeId: "resolve"
                    nodeType: "END"
                    handlerName: "resolveHandler"
                    metadata: {
                        system_prompt: "Confirm to the user that their billing issue has been resolved successfully."
                    }
                }
                {
                    nodeId: "invalid_input"
                    nodeType: "ACTION"
                    handlerName: "invalidHandler"
                    metadata: {
                        system_prompt: "Tell the user their account number was invalid and ask them to try again."
                    }
                }
            ]
            edges: [
                {
                    source: "triage"
                    target: "billing"
                    condition: "ticket_type == 'billing'"
                }
                { source: "billing", target: "verify_billing" }
                # A boolean condition may also be written unquoted: `valid == true`.
                # Quoting only changes how the literal is parsed, not how it is compared, so both
                # forms match the JSON true/false returned by the model.
                {
                    source: "verify_billing"
                    target: "resolve"
                    condition: "valid == 'true'"
                }
                {
                    source: "verify_billing"
                    target: "invalid_input"
                    condition: "valid == 'false'"
                }
                { source: "invalid_input", target: "verify_billing" }
            ]
            config: null
        }
    ) {
        success
        graphId
        message
    }
}

```

#### Robust "Smart Support Agent" graph
```graphql
mutation BuildGraph {
    buildGraph(
        input: {
            graphId: "graph-support-01"
            graphName: "Smart Support Agent"
            nodes: [
                { nodeId: "entry", nodeType: "START", handlerName: "entryHandler" }
                {
                    nodeId: "triage_router"
                    nodeType: "ACTION"
                    handlerName: "triageHandler"
                    metadata: {
                        system_prompt: "You are a support router. Analyze the user input. Classify the intent into one of these categories: 'billing', 'technical', or 'unresolved'. Respond ONLY with a JSON object like {\"ticket_type\": \"category\"}."
                    }
                }
                {
                    nodeId: "billing_ask"
                    nodeType: "ACTION"
                    handlerName: "billingAskHandler"
                    metadata: {
                        system_prompt: "You are a billing specialist. Ask the user for their Account ID to proceed."
                    }
                }
                {
                    nodeId: "billing_wait"
                    nodeType: "HUMAN"
                    handlerName: "billingWaitHandler"
                }
                {
                    nodeId: "billing_validate"
                    nodeType: "ACTION"
                    handlerName: "billingValidateHandler"
                    metadata: {
                        system_prompt: "You are a validator. Check if the input looks like an Account ID (e.g., contains numbers). If yes, respond with {\"is_valid\": \"true\"}. If no, respond with {\"is_valid\": \"false\"}."
                    }
                }
                {
                    nodeId: "billing_retry_ask"
                    nodeType: "ACTION"
                    handlerName: "billingRetryHandler"
                    metadata: {
                        system_prompt: "The user provided an invalid Account ID. Politely tell them it looks incorrect and ask for the CORRECT Account ID one last time."
                    }
                }
                {
                    nodeId: "billing_wait_retry"
                    nodeType: "HUMAN"
                    handlerName: "billingWaitRetryHandler"
                }
                {
                    nodeId: "tech_ask"
                    nodeType: "ACTION"
                    handlerName: "techAskHandler"
                    metadata: {
                        system_prompt: "You are a technical support agent. Ask the user for the Error Code they are seeing."
                    }
                }
                { nodeId: "tech_wait", nodeType: "HUMAN", handlerName: "techWaitHandler" }
                {
                    nodeId: "resolve_success"
                    nodeType: "END"
                    handlerName: "resolveSuccessHandler"
                    metadata: {
                        system_prompt: "Confirm to the user that their issue has been logged and resolved successfully. Be polite."
                    }
                }
                {
                    nodeId: "end_unresolved"
                    nodeType: "END"
                    handlerName: "endUnresolvedHandler"
                    metadata: {
                        system_prompt: "Inform the user that you could not understand their request and escalate it to a human agent."
                    }
                }
            ]
            edges: [
                { source: "entry", target: "triage_router" }
                {
                    source: "triage_router"
                    target: "billing_ask"
                    condition: "ticket_type == 'billing'"
                }
                {
                    source: "triage_router"
                    target: "tech_ask"
                    condition: "ticket_type == 'technical'"
                }
                {
                    source: "triage_router"
                    target: "end_unresolved"
                    condition: "ticket_type == 'unresolved'"
                }
                { source: "billing_ask", target: "billing_wait" }
                { source: "billing_wait", target: "billing_validate" }
                {
                    source: "billing_validate"
                    target: "resolve_success"
                    condition: "is_valid == 'true'"
                }
                {
                    source: "billing_validate"
                    target: "billing_retry_ask"
                    condition: "is_valid == 'false'"
                }
                { source: "billing_retry_ask", target: "billing_wait_retry" }
                { source: "billing_wait_retry", target: "resolve_success" }
                { source: "tech_ask", target: "tech_wait" }
                { source: "tech_wait", target: "resolve_success" }
            ]
        }
    ) {
        success
        graphId
        message
    }
}
```
#### Playlist creator
```graphql

mutation BuildPlaylistGraphFinal {
    buildGraph(
        input: {
            graphId: "playlist-builder-v4"
            graphName: "Deterministic Playlist Agent"
            nodes: [
                # 1. Entry Point
                { nodeId: "entry", nodeType: "START", handlerName: "entryHandler" }

                # 2. Router (Entity Extractor)
                # Python backend now handles the "Intent" logic 100%.
                # This prompt just helps it extract the data.
                {
                    nodeId: "intent_router",
                    nodeType: "ACTION",
                    handlerName: "intentRouterHandler",
                    metadata: {
                        system_prompt: "You are an entity extractor. Extract 'style', 'mood', and 'count' from the user input.\nMerge them with the CURRENT STATE VARIABLES.\nOutput ONLY the updated JSON object.\nExample: {\"style\": \"rock\", \"mood\": \"sad\"}"
                    }
                },

                # 3. Conversational Nodes
                {
                    nodeId: "ask_style",
                    nodeType: "ACTION",
                    handlerName: "askStyleHandler",
                    metadata: {
                        system_prompt: "You are a DJ. Ask the user what music genre they want. Be brief."
                    }
                },
                { nodeId: "wait_style", nodeType: "HUMAN", handlerName: "waitStyleHandler" },

                {
                    nodeId: "ask_mood",
                    nodeType: "ACTION",
                    handlerName: "askMoodHandler",
                    metadata: {
                        system_prompt: "You are a DJ. Ask if they want a 'sad' or 'rebel' mood. Be brief."
                    }
                },
                { nodeId: "wait_mood", nodeType: "HUMAN", handlerName: "waitMoodHandler" },

                {
                    nodeId: "ask_count",
                    nodeType: "ACTION",
                    handlerName: "askCountHandler",
                    metadata: {
                        system_prompt: "You are a DJ. Ask how many songs they want. Be brief."
                    }
                },
                { nodeId: "wait_count", nodeType: "HUMAN", handlerName: "waitCountHandler" },

                # 4. Build Node
                {
                    nodeId: "build_playlist",
                    nodeType: "END",
                    handlerName: "buildPlaylistHandler",
                    metadata: {
                        system_prompt: "Generate a playlist based on the variables. Do not output JSON."
                    }
                },
                {
                    nodeId: "exit_graph",
                    nodeType: "END",
                    handlerName: "exitGraphHandler",
                    metadata: {
                        system_prompt: "Say goodbye."
                    }
                }
            ],
            edges: [
                { source: "entry", target: "intent_router" },

                # Router Logic
                { source: "intent_router", target: "ask_style", condition: "intent == 'ask_style'" },
                { source: "intent_router", target: "ask_mood", condition: "intent == 'ask_mood'" },
                { source: "intent_router", target: "ask_count", condition: "intent == 'ask_count'" },
                { source: "intent_router", target: "build_playlist", condition: "intent == 'build'" },
                { source: "intent_router", target: "exit_graph", condition: "intent == 'exit'" },

                # Cycle
                { source: "ask_style", target: "wait_style" },
                { source: "wait_style", target: "intent_router" },

                { source: "ask_mood", target: "wait_mood" },
                { source: "wait_mood", target: "intent_router" },

                { source: "ask_count", target: "wait_count" },
                { source: "wait_count", target: "intent_router" }
            ]
        }
    ) {
        success
        graphId
        message
    }
}
```
### Sub graphs
#### Car dealer simple example
```graphql
mutation BuildSalesWorkerV8 {
  buildGraph(
    input: {
      graphId: "sales-worker-v8"
      graphName: "Sales Worker - Strict Expert"
      nodes: [
        { nodeId: "worker_entry", nodeType: "START", handlerName: "workerStart" }
        
        # 1. The "Strict" Ask Node
        {
          nodeId: "ask_model",
          nodeType: "ACTION",
          handlerName: "askModelHandler",
          metadata: {
            system_prompt: "You are an expert Car Sales Agent. Your goal is to secure a specific car model.\n\nRules:\n1. If the user mentions a BRAND only (e.g., 'Honda'), you MUST ask for the specific MODEL (e.g., 'Accord' or 'Civic'). Do NOT accept the brand as a final answer.\n2. If the user mentions a specific MODEL (e.g., 'Accord'), acknowledge it and pass to the next step.\n3. Be helpful but persistent about getting the exact model.\n\nCurrent Conversation Context: {input}"
          }
        }
        
        # 2. Wait for User Input
        { nodeId: "wait_model", nodeType: "HUMAN", handlerName: "waitModel" }
        
        # 3. The Confirmation Node
        {
          nodeId: "confirm_sale",
          nodeType: "ACTION",
          handlerName: "confirmHandler",
          metadata: {
            system_prompt: "You are a sales closer. Check the conversation history.\n\n1. If a specific MODEL (e.g., 'Accord') is confirmed, thank the user and ask 'Are you ready to finalize the deal?'.\n2. If the user was vague or you are unsure of the exact model, ask for clarification instead of confirming.\n\nDo not confirm unless a specific model is clearly stated."
          }
        }
        
        # 4. Wait for Confirmation
        { nodeId: "wait_confirmation", nodeType: "HUMAN", handlerName: "waitConfirmation" }
        
        # 5. Router
        {
          nodeId: "check_change_request",
          nodeType: "ACTION",
          handlerName: "checkChangeHandler",
          metadata: {
            system_prompt: "Analyze the user's last input.\n- If they want to change their choice or add something, output JSON: {\"decision\": \"change\"}.\n- If they agree, say 'yes', or 'okay', output JSON: {\"decision\": \"done\"}.\n- Output ONLY the JSON."
          }
        }
        
        # 6. Finalize
        {
          nodeId: "finalize_sale",
          nodeType: "END",
          handlerName: "finalizeHandler",
          metadata: {
            system_prompt: "The deal is closed. Congratulate the user on their new specific car model mentioned in the history and provide a brief, friendly closing statement."
          }
        }
      ],
      edges: [
        { source: "worker_entry", target: "ask_model" },
        { source: "ask_model", target: "wait_model" },
        { source: "wait_model", target: "confirm_sale" },
        { source: "confirm_sale", target: "wait_confirmation" },
        { source: "wait_confirmation", target: "check_change_request" },
        
        # Loop back if change requested
        { source: "check_change_request", target: "ask_model", condition: "decision == 'change'" },
        
        # End if done
        { source: "check_change_request", target: "finalize_sale", condition: "decision == 'done'" }
      ]
    }
  ) {
    success
    graphId
  }
}
mutation BuildManager {
  buildGraph(
    input: {
      graphId: "manager-main-v8"
      graphName: "Dealership Manager "
      nodes: [
        { nodeId: "entry", nodeType: "START", handlerName: "entryHandler" },
        {
          nodeId: "intent_router",
          nodeType: "ACTION",
          handlerName: "routerHandler",
          metadata: {
            system_prompt: "You are a classifier. If the user wants to buy a car, output JSON: {\"intent\": \"sales\"}. Otherwise output {\"intent\": \"support\"}. Output ONLY JSON."
          }
        },
        {
          nodeId: "handle_sales",
          nodeType: "GRAPH",
          handlerName: "salesSubgraphHandler",
          metadata: {
            subgraph_id: "sales-worker-v8" # Points to the new worker
          }
        },
        {
          nodeId: "support_node",
          nodeType: "END",
          handlerName: "supportHandler",
          metadata: { system_prompt: "Transferring to support..." }
        }
      ],
      edges: [
        { source: "entry", target: "intent_router" },
        { source: "intent_router", target: "handle_sales", condition: "intent == 'sales'" },
        { source: "intent_router", target: "support_node", condition: "intent == 'support'" }
      ]
    }
  ) {
    success
  }
}

#### Attachments: read a spreadsheet or a word document

A `DOCUMENT` node is deterministic — it never calls a model. It fetches the file the run
names, extracts its text, and writes it to `variables.document_text`; the `ACTION` node after
it is what actually reasons over that text. Details in
[B2B_INTEGRATION.md §7](B2B_INTEGRATION.md).

Build a graph with one:

```http request
POST http://localhost:9191/graphql

{
  "query": "mutation BuildGraph($input: BuildGraphInput!) { buildGraph(input: $input) { success graphId message } }",
  "variables": {
    "input": {
      "graphId": "invoice-agent",
      "graphName": "Invoice agent",
      "nodes": [
        { "nodeId": "start",   "nodeType": "START",    "handlerName": "startHandler" },
        { "nodeId": "attach",  "nodeType": "DOCUMENT", "handlerName": "attachHandler",
          "metadata": { "label": "Read the attached file",
                        "max_rows_per_sheet": 200,
                        "max_total_chars": 15000 } },
        { "nodeId": "summarise", "nodeType": "ACTION", "handlerName": "summariseHandler",
          "metadata": { "system_prompt": "You receive spreadsheet rows in variables.document_text. Reply with JSON only: {\"total\": number, \"top_client\": string}." } },
        { "nodeId": "end",     "nodeType": "END",      "handlerName": "endHandler" }
      ],
      "edges": [
        { "source": "start", "target": "attach" },
        { "source": "attach", "target": "summarise" },
        { "source": "summarise", "target": "end" }
      ]
    }
  }
}
```

Then, for B2B, upload the file and quote its id in the run's context
(see [B2B_INTEGRATION.md §7](B2B_INTEGRATION.md) for the inline one-call form):

```bash
grpcurl -proto external_api.proto -plaintext -H 'x-api-key: <key>' -d '{
  "thread_id": "t-1", "filename": "q1.xlsx",
  "mime_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  "content": "'$(base64 -w0 q1.xlsx)'"
}' localhost:9091 external.v1.LangGraphExternalApi/UploadDocument

grpcurl -proto external_api.proto -plaintext -H 'x-api-key: <key>' -d '{
  "graph_id": "invoice-agent", "thread_id": "t-1", "input": "Summarise the attached file",
  "context": { "document_ref": "<document_id>" }
}' localhost:9091 external.v1.LangGraphExternalApi/ExecuteGraph
```

```
#### Attachments: compare two spreadsheets in one run

A graph that reads **several** files gives each `DOCUMENT` node a slot of its own — the
context key its file arrives under, the variable its text lands in, and a label. "Which file
is which" is then answered by the graph, not guessed by the model. Two nodes left on the
default key would read the *same* file twice, so naming the keys is what makes this graph
work at all.

```http request
POST http://localhost:9191/graphql

{
  "query": "mutation BuildGraph($input: BuildGraphInput!) { buildGraph(input: $input) { success graphId message } }",
  "variables": {
    "input": {
      "graphId": "discrepancy-agent",
      "graphName": "Report discrepancies",
      "nodes": [
        { "nodeId": "start",     "nodeType": "START",    "handlerName": "startHandler" },
        { "nodeId": "timesheet", "nodeType": "DOCUMENT", "handlerName": "timesheetHandler",
          "metadata": { "label": "Time sheet (as submitted)",
                        "document_ref_key": "timesheets_ref",
                        "output_variable": "timesheets_text" } },
        { "nodeId": "report",    "nodeType": "DOCUMENT", "handlerName": "reportHandler",
          "metadata": { "label": "Filled report",
                        "document_ref_key": "report_ref",
                        "output_variable": "report_text" } },
        { "nodeId": "compare",   "nodeType": "ACTION",   "handlerName": "compareHandler",
          "metadata": { "system_prompt": "You receive two files: variables.timesheets_text (what the person submitted) and variables.report_text (the filled-in report). List every discrepancy as JSON only: [{\\\"field\\\": string, \\\"timesheet\\\": string, \\\"report\\\": string}]. Use [] when there are none." } },
        { "nodeId": "end",       "nodeType": "END",      "handlerName": "endHandler" }
      ],
      "edges": [
        { "source": "start", "target": "timesheet" },
        { "source": "timesheet", "target": "report" },
        { "source": "report", "target": "compare" },
        { "source": "compare", "target": "end" }
      ]
    }
  }
}
```

Upload each file, then name both references in **one** run's context:

```bash
# Two uploads, because the two files are business data a run will quote.
grpcurl -proto external_api.proto -plaintext -H 'x-api-key: <key>' -d '{
  "thread_id": "t-3", "filename": "timesheet.xlsx",
  "mime_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  "content": "'$(base64 -w0 timesheet.xlsx)'"
}' localhost:9091 external.v1.LangGraphExternalApi/UploadDocument
grpcurl -proto external_api.proto -plaintext -H 'x-api-key: <key>' -d '{
  "thread_id": "t-3", "filename": "report.xlsx",
  "mime_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  "content": "'$(base64 -w0 report.xlsx)'"
}' localhost:9091 external.v1.LangGraphExternalApi/UploadDocument

# One run, both references - the keys are the graph's, and each is tenant-checked.
grpcurl -proto external_api.proto -plaintext -H 'x-api-key: <key>' -d '{
  "graph_id": "discrepancy-agent", "thread_id": "t-3",
  "input": "Compare the two files and list the discrepancies",
  "context": { "timesheets_ref": "<document_id 1>", "report_ref": "<document_id 2>" }
}' localhost:9091 external.v1.LangGraphExternalApi/ExecuteGraph
```

The chat panel does the same thing through the browser: it reads those slots and shows one
labelled 📎 picker per `DOCUMENT` node, so both files are attached before a single run.

Leave out one of the two references and the empty node reports
`error = missing_context:report_ref` and stops — it never answers from one file alone.

