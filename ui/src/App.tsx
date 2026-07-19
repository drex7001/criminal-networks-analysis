import { AuthGuard } from "./auth/AuthGuard";
import { Shell } from "./layout/Shell";
import { ROUTES, activeRoute, usePath } from "./routing";
import { GraphView } from "./views/GraphView";
import { SourcesView } from "./views/SourcesView";

export function App() {
  const route = activeRoute(usePath());

  return (
    <AuthGuard>
      <Shell route={route}>
        {route === ROUTES.sources ? <SourcesView /> : <GraphView />}
      </Shell>
    </AuthGuard>
  );
}
