# Request flow
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
                { "nodeId": "node-1", "nodeType": "START", "handlerName": "startHandler" },
                { "nodeId": "node-2", "nodeType": "END", "handlerName": "endHandler" }
            ],
            "edges": [
                { "source": "node-1", "target": "node-2" }
            ]
        }
    }
}
```

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

mutation BuildPlaylistGraph {
    buildGraph(
        input: {
            graphId: "playlist-builder-01"
            graphName: "Personalized Playlist Generator"
            nodes: [
                # Entry point: Start only if user explicitly asks for playlist creation
                { nodeId: "entry", nodeType: "START", handlerName: "entryHandler" }

                # Ask for style/genre
                {
                    nodeId: "ask_style_genre"
                    nodeType: "ACTION"
                    handlerName: "askStyleGenreHandler"
                    metadata: {
                        system_prompt: "Ask the user: 'What style or genre of music are you in the mood for? (e.g., rock, jazz, electronic)'"
                    }
                }

                # Ask for mood (sad or rebel)
                {
                    nodeId: "ask_mood"
                    nodeType: "ACTION"
                    handlerName: "askMoodHandler"
                    metadata: {
                        system_prompt: "Ask the user: 'Do you want a sad or rebel playlist?'"
                    }
                }

                # Decision point for randomness
                {
                    nodeId: "random_choice"
                    nodeType: "ACTION"
                    handlerName: "randomChoiceHandler"
                    metadata: {
                        system_prompt: "Check if the user wants random parameters. Respond with {\"choice\": \"random\"} if they say 'random', 'creative', 'impress', or 'surprise me'. Respond with {\"choice\": \"specific\"} otherwise."
                    }
                }

                # If user says "I don't know" or similar, ask for randomness
                {
                    nodeId: "ask_random"
                    nodeType: "ACTION"
                    handlerName: "askRandomHandler"
                    metadata: {
                        system_prompt: "Ask: 'Should I choose random parameters for you?' If yes, set randomChoice = true. If no, set randomChoice = false."
                    }
                }

                # Automatic randomness if user says "creative" or "impress"
                {
                    nodeId: "random_auto"
                    nodeType: "ACTION"
                    handlerName: "randomAutoHandler"
                    metadata: {
                        system_prompt: "Set randomChoice = true and proceed to ask_count."
                    }
                }

                # Exit if user declines random
                {
                    nodeId: "end_no_random"
                    nodeType: "END"
                    handlerName: "endNoRandomHandler"
                    metadata: {
                        system_prompt: "Inform the user: 'Okay, let’s stick to specific parameters. Please tell me your preferred style and mood again.'"
                    }
                }

                # Ask for number of songs
                {
                    nodeId: "ask_count"
                    nodeType: "ACTION"
                    handlerName: "askCountHandler"
                    metadata: {
                        system_prompt: "Ask: 'How many songs do you want in your playlist?'"
                    }
                }

                # Build the playlist
                {
                    nodeId: "build_playlist"
                    nodeType: "ACTION"
                    handlerName: "buildPlaylistHandler"
                    metadata: {
                        system_prompt: "Generate a playlist based on the gathered parameters (style, mood, count) or random if chosen. Respond with the playlist in a user-friendly format."
                    }
                }

                # Exit if user doesn't want a playlist
                {
                    nodeId: "exit_graph"
                    nodeType: "END"
                    handlerName: "exitGraphHandler"
                    metadata: {
                        system_prompt: "Inform the user: 'No worries! Let me know if you’d like a playlist later.'"
                    }
                }
            ]
            edges: [
                # Start only if user asks for playlist
                { source: "entry", target: "ask_style_genre", condition: "user_input.contains('playlist')" }

                # Exit if user doesn't ask for playlist
                { source: "entry", target: "exit_graph", condition: "!user_input.contains('playlist')" }

                # Style/genre → mood
                { source: "ask_style_genre", target: "ask_mood" }

                # Mood → decision point
                { source: "ask_mood", target: "random_choice" }

                # Decision point branches
                {
                    source: "random_choice"
                    target: "random_auto"
                    condition: "user_input.contains('creative') || user_input.contains('impress')"
                }
                {
                    source: "random_choice"
                    target: "ask_random"
                    condition: "user_input.contains('know') || user_input.contains('unsure')"
                }
                {
                    source: "random_choice"
                    target: "end_no_random"
                    condition: "user_input.contains('no') || user_input.contains('specific')"
                }

                # Randomness paths
                { source: "ask_random", target: "ask_count", condition: "randomChoice == true" }
                { source: "random_auto", target: "ask_count" }

                # If user declines random, loop back to ask_style_genre
                { source: "end_no_random", target: "ask_style_genre" }

                # Count → build playlist
                { source: "ask_count", target: "build_playlist" }

                # Exit after building
                { source: "build_playlist", target: "exit_graph" }
            ]
        }
    ) {
        success
        graphId
        message
    }
}
```
