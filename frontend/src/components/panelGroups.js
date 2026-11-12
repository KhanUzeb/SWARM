// PANEL_GROUPS owns the Places/Connect destination list, and nothing keeps a
// second copy. A group *is* a destination: the user arrives at "Places" or
// "Connect" from the sidebar, the view strip or the palette, and the tabs of
// that group are the surfaces inside it. There is deliberately no group tab
// strip here - it would compete with the navigation that chose the group.
//
// It lives in its own module so the shell navigation (Sidebar, TopBar) can
// read the destination list without statically importing ComputerPanel.jsx —
// the panel itself is lazy-loaded from App.jsx, and a static import from the
// shell would pull it into the main chunk and defeat the split.
export const PANEL_GROUPS = [
  { id: "places", label: "Places", tabs: [
    { id: "files", label: "Sandbox" },
    { id: "system", label: "System" },
    { id: "browser", label: "Browser" },
  ]},
  { id: "connect", label: "Connect", tabs: [
    { id: "ai", label: "AI" },
    { id: "apps", label: "Apps" },
    { id: "tools", label: "Tools" },
    { id: "plugins", label: "Plugins" },
  ]},
];
