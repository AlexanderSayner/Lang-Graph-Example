import React from 'react';
import { ApolloProvider } from '@apollo/client';
import { client } from './apollo-client';
import GraphViewer from './GraphViewer';

function App() {
  return (
    <ApolloProvider client={client}>
      <div className="App">
        <GraphViewer />
      </div>
    </ApolloProvider>
  );
}

export default App;
