// Browser history integration. Each in-app screen (and each wizard step) is a real history entry, so the phone's Back
// gesture / hardware Back / browser Back walk exactly the path the operator took, and reload restores the same screen.
//
// history.state = { idx } counts entries inside the app; idx 0 is where the app was entered. That lets the in-app Back
// button use history.back() when there is somewhere in the app to go back to, and fall back to a "parent" screen when
// the operator arrived by deep link / reload at idx 0 (otherwise Back would leave the app).

export function createNavigator(win, { onNavigate }) {
  const hist = win.history;
  let idx = 0;
  let pendingReplace = null;

  const currentHash = () => win.location.hash || "";

  function emit(hash, meta) { onNavigate(hash, meta); }

  return {
    get depth() { return idx; },
    hash: currentHash,

    start(defaultHash) {
      const st = hist.state;
      idx = st && Number.isInteger(st.idx) ? st.idx : 0;
      let hash = currentHash();
      if (!hash || hash === "#" || hash === "#/") hash = defaultHash || "#/";
      hist.replaceState({ idx }, "", hash);
      win.addEventListener("popstate", (ev) => {
        const s = ev.state;
        idx = s && Number.isInteger(s.idx) ? s.idx : 0;
        if (pendingReplace) {
          // Finishing a multi-step flow: we walked back past its steps; now swap the landing entry for the result screen.
          const target = pendingReplace;
          pendingReplace = null;
          hist.replaceState({ idx }, "", target);
          emit(target, { replace: true });
          return;
        }
        emit(currentHash() || "#/", { popped: true });
      });
      emit(hash, { initial: true });
    },

    go(hash) {
      if (hash === currentHash()) { emit(hash, { same: true }); return; }
      idx += 1;
      hist.pushState({ idx }, "", hash);
      emit(hash, { push: true });
    },

    replace(hash) {
      hist.replaceState({ idx }, "", hash);
      emit(hash, { replace: true });
    },

    // After a completed wizard, drop its step entries from the Back path: go back `steps` entries, then show `hash` there.
    unwind(steps, hash) {
      const n = Math.min(Math.max(0, steps | 0), idx);
      if (n <= 0) { this.replace(hash); return; }
      pendingReplace = hash;
      hist.go(-n);
    },

    back(fallbackHash) {
      if (idx > 0) { hist.back(); return; }
      this.replace(fallbackHash || "#/");
    },
  };
}
