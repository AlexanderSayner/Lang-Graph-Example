import { ApolloClient, InMemoryCache, gql, createHttpLink } from '@apollo/client';
import { GraphQLWsLink } from '@apollo/client/link/subscriptions';
import { createClient } from 'graphql-ws';

const GRAPHQL_ENDPOINT = 'http://localhost:8080/graphql';
const WS_ENDPOINT = 'ws://localhost:8080/graphql';

// Create HTTP link for queries and mutations
const httpLink = createHttpLink({
  uri: GRAPHQL_ENDPOINT,
});

// Create WebSocket link for subscriptions
const wsLink = new GraphQLWsLink(
  createClient({
    url: WS_ENDPOINT,
  })
);

// Apollo Client setup
export const client = new ApolloClient({
  link: httpLink.concat(wsLink),
  cache: new InMemoryCache(),
  defaultOptions: {
    query: {
      fetchPolicy: 'no-cache',
    },
    mutate: {
      fetchPolicy: 'no-cache',
    },
  },
});

// GraphQL Queries and Mutations
export const LIST_GRAPHS = gql`
  query ListGraphs($pageSize: Int!) {
    listGraphs(pageSize: $pageSize) {
      graphs {
        graphId
        graphName
        nodeCount
        status
      }
      pageInfo {
        hasNextPage
        endCursor
      }
      totalCount
    }
  }
`;

export const BUILD_GRAPH = gql`
  mutation BuildGraph($input: BuildGraphInput!) {
    buildGraph(input: $input) {
      success
      graphId
      message
    }
  }
`;

export const EXECUTE_GRAPH = gql`
  mutation ExecuteGraph($input: ExecuteGraphInput!) {
    executeGraph(input: $input) {
      success
      output
      state
      errorMessage
    }
  }
`;

export const EXECUTE_GRAPH_STREAM = gql`
  subscription ExecuteGraphStream($input: ExecuteGraphInput!) {
    executeGraphStream(input: $input) {
      eventType
      nodeId
      output
      state
      timestamp
      errorMessage
    }
  }
`;

export const GET_GRAPH_STATE = gql`
  query GetGraphState($graphId: String!, $threadId: String) {
    getGraphState(graphId: $graphId, threadId: $threadId) {
      success
      state
      currentNode
      nodeHistory
    }
  }
`;

export const DELETE_GRAPH = gql`
  mutation DeleteGraph($graphId: String!) {
    deleteGraph(graphId: $graphId) {
      success
      message
    }
  }
`;
