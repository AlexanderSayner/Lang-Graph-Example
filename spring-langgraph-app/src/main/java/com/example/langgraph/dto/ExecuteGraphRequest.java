package com.example.langgraph.dto;

import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.util.List;
import java.util.Map;

/**
 * DTO for executing a graph.
 */
@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class ExecuteGraphRequest {
    private String graphId;
    private String input;
    private Map<String, String> context;
    private boolean streamOutput;
}
