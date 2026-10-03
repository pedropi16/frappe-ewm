import { h } from "#wms/ui/dom.js";
import { load, update } from "#wms/app.js";
import { api } from "#wms/core/api.js";
import { Empty, Loading } from "#wms/ui/kit.js";
import { sectionCrumb, sectionHash } from "#wms/screens/shared.js";

// The "open items" list shared by Receive, Ship, Pack, Count, Quality, Load, Kitting, Consolidation, VAS, Handling Units.
// Detail screens live at "<id>/:name" and find their row in the list by name, so reload and deep links work.
export function listScreen({ id, pattern, title, section, method, args, empty, card, header, actions, extraEnter }) {
  const st = { rows: [], loading: false, loaded: false };
  async function refresh(ctx) {
    st.loading = true; update();
    const rows = await load(() => api(method, args ? args(ctx) : {}, { read: true }));
    st.loading = false; st.loaded = true;
    if (rows) st.rows = rows;
    update();
  }
  return {
    id, pattern, st, refresh,
    title: () => title,
    crumb: () => sectionCrumb(section),
    parent: () => sectionHash(section),
    async enter(ctx) { if (extraEnter) extraEnter(ctx); await refresh(ctx); },
    render(ctx) {
      const wrap = h("div");
      if (header) wrap.append(header(ctx, st));
      if (st.loading && !st.loaded) { wrap.append(Loading()); return wrap; }
      if (!st.rows.length) { wrap.append(Empty(empty)); return wrap; }
      st.rows.forEach((r) => wrap.append(card(r, ctx)));
      return wrap;
    },
    actions: actions || (() => null),
  };
}

// Finds one row of a list by name, loading the list first when a reload landed straight on a detail screen.
export async function findRow(list, name, ctx) {
  if (!list.st.rows.some((r) => r.name === name)) await list.refresh(ctx || {});
  return list.st.rows.find((r) => r.name === name) || null;
}
