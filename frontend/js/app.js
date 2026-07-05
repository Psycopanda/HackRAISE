// Application entry point: instantiates the three views and wires navigation.

import { initHome } from "./home.js";
import { initCreate } from "./create.js";
import { initWorkspace } from "./workspace.js";
import { showView } from "./ui.js";

const workspace = initWorkspace();

const create = initCreate({
  onEnterWorkspace: (result) => workspace.start(result),
  onBack: () => showView("view-home"),
});

initHome({
  onCreate: (result) => create.start(result),
  onJoined: (result) => workspace.start(result),
});

showView("view-home");
