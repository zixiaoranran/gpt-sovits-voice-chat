/* 本页的全部前端逻辑（与样式表 ui.css 配套）。由 FastAPI 路由 /vc_ui.js 每次请求现读磁盘，
   所以改完这里只要刷新浏览器（Ctrl+F5）即可生效，不用重启服务。

   为什么布局改动都用「内联 !important」写 style：
   Gradio 会把自定义 CSS 复制一份并加前缀（gradio-app .gradio-container… .contain #…），
   那份副本优先级高得多，任何写在样式表里的覆盖（类名、变量）都可能被它压过 ——
   实测到的现象就是“侧栏只淡出、宽度不让、展开按钮也不出现”。
   内联样式的 !important 优先级高于所有作者样式表规则，改它才是稳的。
   宽度等属性仍在 ui.css 里声明 transition，所以改成内联值同样是平滑动画。 */

function __vcInitSide() {
// 收起时侧栏要变成的几何；展开时这些内联值全部清掉（回到样式表里的默认值）
  const SIDE_ON = { "width": "0px", "max-width": "0px", "flex-basis": "0px",
                    "min-width": "0px", "padding-left": "0px", "padding-right": "0px",
                    "opacity": "0", "border-right-width": "0px" };
  const SHOW_ON = { "width": "40px", "max-width": "40px", "margin": "8px 0 0 10px",
                    "opacity": "1", "pointer-events": "auto" };

  // 幂等：值已经对了就一个字节都不改。
  // 这很重要 —— 外面有个 MutationObserver 盯着 style，若这里无条件重写，
  // 每次写都会再触发 observer，形成死循环把页面卡住。
  const apply = (el, spec) => {
    if (!el) return;
    Object.keys(spec).forEach((p) => {
      if (el.style.getPropertyValue(p) !== spec[p] ||
          el.style.getPropertyPriority(p) !== "important") {
        el.style.setProperty(p, spec[p], "important");
      }
    });
  };
  const clear = (el, props) => {
    if (!el) return;
    props.forEach((p) => { if (el.style.getPropertyValue(p)) el.style.removeProperty(p); });
  };
  const sideProps = Object.keys(SIDE_ON);
  const showProps = Object.keys(SHOW_ON);

  window.__vcSetSide = (on, instant) => {
    const sb = document.getElementById("vc_sidebar");
    const sh = document.getElementById("vc_show");
    // 初始化恢复时不要播过渡，否则每次刷新都会看到侧栏"滑"一下
    const setAnim = (el, off) => {
      if (!el) return;
      if (off) el.style.setProperty("transition", "none", "important");
      else el.style.removeProperty("transition");
    };
    if (instant) { setAnim(sb, true); setAnim(sh, true); }
    if (on) { apply(sb, SIDE_ON); apply(sh, SHOW_ON); }
    else { clear(sb, sideProps); clear(sh, showProps); }
    document.body.classList.toggle("vc_collapsed", !!on);
    // 记住这一次的状态，刷新后按它恢复（和主题一样的做法）
    try { localStorage.setItem("vc_side", on ? "1" : "0"); } catch (e) {}
    if (instant) {
      setTimeout(() => { setAnim(sb, false); setAnim(sh, false); }, 80);
    }
    if (window.__vcPlaceMenu) window.__vcPlaceMenu();
    return !!on;
  };

  // 「⋮」菜单定位：贴在触发它的那个「⋮」正下方、左缘对齐（文字正好落在「⋮」正下方），
  // 不盖住「⋮」本身（否则想再点一下关掉都点不到）。用 fixed 定位，不会被列表 overflow 裁掉。
  window.__vcPlaceMenu = () => {
    const menu = document.getElementById("vc_menu_pop");
    if (!menu) return;
    // 宽度收到「文字宽」：Gradio 的包装层带 min-width、会话列表还会把直接子元素拉成 100%，
    // 所以这里直接量文字本身的宽度，再用内联 !important 定死（内联 !important 优先级最高），
    // 保证文字后面只有正常的 padding，不留一大截空白。
    const btns = menu.querySelectorAll("button");
    let tw = 0;
    btns.forEach((b) => {
      b.style.setProperty("min-width", "0", "important");
      b.style.setProperty("width", "100%", "important");
      try {
        const r = document.createRange();
        r.selectNodeContents(b);
        tw = Math.max(tw, r.getBoundingClientRect().width);
      } catch (e) { /* 量不到就交给 CSS 的 max-content */ }
    });
    if (tw) {
      // 按钮左右 padding 16 + 菜单左右 padding 8 + 边框 2
      const W = Math.ceil(tw) + 26;
      menu.style.setProperty("box-sizing", "border-box", "important");
      menu.style.setProperty("width", W + "px", "important");
      menu.style.setProperty("min-width", "0", "important");
      menu.style.setProperty("max-width", "none", "important");
    }
    // 菜单在 DOM 里紧跟触发它的那一行；往前找到最近的一个会话行
    let row = menu.previousElementSibling;
    while (row && !row.classList.contains("vc_convrow")) row = row.previousElementSibling;
    const dots = row && row.querySelector(".vc_dots");
    if (!dots) return;
    const d = dots.getBoundingClientRect();
    const w = menu.offsetWidth || 120;
    const h = menu.offsetHeight || 120;
    // 左缘对齐「⋮」左缘 → 菜单文字正好落在「⋮」正下方；贴右边界时再往左收
    const left = Math.max(4, Math.min(d.left, innerWidth - w - 4));
    // 下方放不下就翻到「⋮」上方，避免贴底时超出屏幕
    const below = d.bottom + 4;
    const top = (below + h > innerHeight - 4) ? Math.max(4, d.top - h - 4) : below;
    menu.style.setProperty("position", "fixed", "important");
    menu.style.setProperty("left", left + "px", "important");
    menu.style.setProperty("right", "auto", "important");
    menu.style.setProperty("top", top + "px", "important");
  };
  // Gradio 每次刷新会话列表都会重设它自己管的 min-width/flex-grow，
  // 那会把我写的内联覆盖冲掉；这里盯着 style 变化，收起状态下被改了就再压回去。
  if (!window.__vcSideObs) {
    window.__vcSideObs = new MutationObserver(() => {
      if (document.body.classList.contains("vc_collapsed")) window.__vcSetSide(true);
    });
    ["vc_sidebar", "vc_show"].forEach((id) => {
      const el = document.getElementById(id);
      if (el) window.__vcSideObs.observe(el, { attributes: true,
                                               attributeFilter: ["style"] });
    });
  }
  // 收起/展开按钮：用原生事件委托绑定（挂在 document 上）。
  // Gradio 重渲染会把元素换掉，直接绑在按钮上会失效；委托则永远有效。
  // 也正因为不依赖 Gradio 事件，服务端不用再加对应的事件绑定。
  if (!window.__vcSideBound) {
    window.__vcSideBound = "1";
    document.addEventListener("click", (ev) => {
      const t = ev.target;
      if (!t || !t.closest) return;
      const btn = t.closest("#vc_collapsebtn") || t.closest("#vc_show");
      if (!btn) return;
      const now = !document.body.classList.contains("vc_collapsed");
      window.__vcSetSide(now);
    }, true);
  }
  // 菜单是每次打开时新渲染出来的，渲染完要重新定位一次；
  // 列表滚动/窗口尺寸变化时也要跟着动，否则会飘到别处。
  if (!window.__vcMenuObs) {
    const relayout = () => { if (window.__vcPlaceMenu) window.__vcPlaceMenu(); };
    window.__vcMenuObs = new MutationObserver(relayout);
    const list = document.getElementById("vc_convlist");
    if (list) window.__vcMenuObs.observe(list, { childList: true, subtree: true });
    addEventListener("resize", relayout);
    addEventListener("scroll", relayout, true);
  }
  // 按上次离开时的状态恢复侧栏（上一次收起，这次就还是收起；首帧不播动画）
  let savedSide = "0";
  try { savedSide = localStorage.getItem("vc_side") || "0"; } catch (e) {}
  window.__vcSetSide(savedSide === "1", true);
}

function __vcInitMsgBar() {
  if (window.__vcMsgBarOn) { if (window.__vcMsgBarSync) window.__vcMsgBarSync(); return; }
  window.__vcMsgBarOn = true;
  const defs = [["⟳ 重新加载", "regen"], ["✎ 改写", "edit"],
                ["⧉ 复制", "copy"], ["🗑 删除", "del"]];

  const wrapEl = () => document.querySelector("#vc_chatbox .message-wrap");
  const bubbleOf = (row) =>
    (row && row.querySelector) ? row.querySelector("button[data-testid]") : null;
  const roleOf = (row) => {
    const b = bubbleOf(row);
    return (b && b.getAttribute("data-testid") === "user") ? "user" : "bot";
  };
  // 轮次 = 这条消息之前（若自己是提问则含自己）出现过几条「提问」气泡。
  // 后端 messages 是 [[提问, 回答], ...]，所以第 k 条提问就是第 k 轮。
  const turnOf = (row) => {
    const wrap = row ? row.parentElement : null;
    if (!wrap) return -1;
    let n = -1;
    for (const ch of wrap.children) {
      if (!ch.classList || !ch.classList.contains("message-row")) continue;
      const b = bubbleOf(ch);
      if (b && b.getAttribute("data-testid") === "user") n++;
      if (ch === row) return n;
    }
    return -1;
  };
  const textOf = (row) => {
    const b = bubbleOf(row);
    if (!b) return "";
    const md = b.querySelector(".prose") || b;
    return (md.innerText || md.textContent || "").trim();
  };
  const stop = (e) => { e.preventDefault(); e.stopPropagation(); };
  const sideOf = (role) => (role === "user" ? "vc-user" : "vc-bot");
  const mkbtn = (text, cls, onClick) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "vc-mbtn" + (cls ? " " + cls : "");
    b.textContent = text;
    b.addEventListener("click", (e) => { stop(e); onClick(b); });
    return b;
  };

  // 服务端通过隐藏的 #vc_branchinfo 告诉我们：哪轮回答有语音（a=播放地址）、有几个版本（n/cur）
  const infoOf = () => {
    const el = document.getElementById("vc_branchinfo");
    if (!el) return {};
    try { return (JSON.parse((el.textContent || "").trim()) || {}).turns || {}; }
    catch (e) { return {}; }
  };

  // 播放某条回答的语音：不在这里自己播，而是交给下方那个「AI 语音回复」大播放器 ——
  // 把这条回答的 wav 塞给它，波形就出现在原来的位置，也更清楚。
  // 实现：前端点一下隐藏按钮，把 {op:"play", turn} 交给后端 on_message_action，
  // 后端把该轮语音写进 audio 组件（它 autoplay=True，会自动播放并显示大波形）。


  // 把操作交给后端：写进全局 -> 点隐藏按钮。★ 不能在这之后立刻清掉 __vcMsgOp ——
  // Gradio 的 click 事件是异步走一遍事件管线，那个 js 要过一拍才执行，
  // 同步清掉的话它读到的就是空值。每次点都会重新赋值，所以留着上一次的值没有影响。
  // 注意 elem_id 就在 <button> 本身上（Gradio 的 Button 没有外层 div），别写 #id button。
  const fire = (spec) => {
    const host = document.getElementById("vc_msgop_btn");
    const btn = host && (host.tagName === "BUTTON" ? host : host.querySelector("button"));
    if (!btn) { console.warn("[vc] 没找到 #vc_msgop_btn，消息操作无法送达后端"); return; }
    window.__vcMsgOp = spec;
    btn.click();
  };

  // 改写：就地换成「输入框 + 保存 / 取消」
  const openEditor = (bar, row, turn, role) => {
    const wrap = wrapEl();
    if (!wrap || wrap.querySelector(".vc-editbox")) return;
    const box = document.createElement("div");
    box.className = "vc-editbox " + sideOf(role);
    const ta = document.createElement("textarea");
    ta.className = "vc-editta";
    ta.value = textOf(row);
    const row2 = document.createElement("div");
    row2.className = "vc-msgbar " + sideOf(role);
    const close = () => { box.remove(); bar.style.display = ""; };
    row2.appendChild(mkbtn("✔ 保存", "", () => {
      const t = (ta.value || "").trim();
      if (!t) { ta.focus(); return; }
      close();
      fire({ op: "edit", turn: turn, role: role, text: t });
    }));
    row2.appendChild(mkbtn("✕ 取消", "", () => close()));
    ta.addEventListener("keydown", (e) => {      // Ctrl+Enter 保存 / Esc 取消
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { stop(e); row2.firstChild.click(); }
      else if (e.key === "Escape") { stop(e); close(); }
    });
    box.appendChild(ta); box.appendChild(row2);
    bar.style.display = "none";
    wrap.insertBefore(box, bar);
    ta.focus();
    try { ta.setSelectionRange(ta.value.length, ta.value.length); } catch (e) {}
  };

  // 删除：先原地反问一句，确认后才真的删（二次确认）
  const openConfirm = (bar, row, turn, role) => {
    const wrap = wrapEl();
    if (!wrap || wrap.querySelector(".vc-confirm")) return;
    const box = document.createElement("div");
    box.className = "vc-msgbar vc-confirm " + sideOf(role);
    const q = document.createElement("span");
    q.className = "vc-q";
    q.textContent = (role === "user") ? "连同它的回答和语音一起删除？" : "删除这条回答和它的语音？";
    const close = () => { box.remove(); bar.style.display = ""; };
    box.appendChild(q);
    box.appendChild(mkbtn("✔ 确认删除", "vc-danger", () => {
      close();
      fire({ op: "del", turn: turn, role: role });
    }));
    box.appendChild(mkbtn("✕ 取消", "", () => close()));
    bar.style.display = "none";
    wrap.insertBefore(box, bar);
  };

  const makeBar = (row, turn, role, item, sig) => {
    const bar = document.createElement("div");
    bar.className = "vc-msgbar " + sideOf(role);
    bar.dataset.sig = sig;
    // ▶ 播放这条回答的语音：交给下方大播放器显示波形并播放（只有合成过语音的回答才有）
    if (role === "bot" && item.a) {
      const p = mkbtn("▶", "vc-play", () => fire({ op: "play", turn: turn, role: role }));
      p.title = "在下方播放这条回答的语音（显示波形）";
      bar.appendChild(p);
    }
    // < 2/2 > 切换「重新加载」产生的多个回答版本（版本属于「回答」，所以只挂在回答那条上）
    if (role === "bot" && item.n > 1) {
      const wrapv = document.createElement("span");
      wrapv.className = "vc-ver";
      wrapv.appendChild(mkbtn("<", "vc-verbtn", () => {
        fire({ op: "ver", turn: turn, role: role, d: -1 });
      }));
      const num = document.createElement("span");
      num.className = "vc-vernum";
      num.textContent = item.cur + "/" + item.n;
      num.title = "这是第 " + item.cur + " 个回答，共 " + item.n + " 个";
      wrapv.appendChild(num);
      wrapv.appendChild(mkbtn(">", "vc-verbtn", () => {
        fire({ op: "ver", turn: turn, role: role, d: 1 });
      }));
      bar.appendChild(wrapv);
    }
    defs.forEach((d) => {
      bar.appendChild(mkbtn(d[0], "", (b) => {
        const op = d[1];
        if (op === "copy") { doCopy(row, b); return; }
        if (op === "edit") { openEditor(bar, row, turn, role); return; }
        if (op === "del") { openConfirm(bar, row, turn, role); return; }
        fire({ op: op, turn: turn, role: role });
      }));
    });
    return bar;
  };

  const fallbackCopy = (t, done) => {
    const ta = document.createElement("textarea");
    ta.value = t;
    ta.style.position = "fixed"; ta.style.top = "-1000px"; ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand("copy"); } catch (e) {}
    ta.remove();
    done();
  };
  const doCopy = (row, btn) => {
    const t = textOf(row);
    if (!t) return;
    const done = () => {
      btn.textContent = "✓ 已复制";
      setTimeout(() => { btn.textContent = "⧉ 复制"; }, 900);
    };
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(t).then(done, () => fallbackCopy(t, done));
        return;
      }
    } catch (e) {}
    fallbackCopy(t, done);   // http 非 localhost 下没有 clipboard API，用 execCommand 兜底
  };

  // 工具栏挂在「这一行气泡的下面」，作为 .message-wrap 的直接子元素 ——
  // 不再塞进 .message 里面（那样会被气泡的框包住，看着和对话连在一起）。
  const sync = () => {
    const wrap = wrapEl();
    if (!wrap) return;
    const info = infoOf();
    const rows = [...wrap.querySelectorAll(":scope > .message-row")];
    const keep = new Set();
    for (const row of rows) {
      const role = roleOf(row);
      const turn = turnOf(row);
      const txt = textOf(row);
      // 「……」= 正在生成；空 = 这条回答刚被删掉 —— 都不该留按钮，连气泡一起藏掉
      if (role === "bot" && (txt === "" || txt === "……")) {
        row.classList.add("vc-empty");
        if (row.__vcBar) { row.__vcBar.remove(); row.__vcBar = null; }
        continue;
      }
      row.classList.remove("vc-empty");
      const item = info[String(turn)] || {};
      const sig = [role, turn, item.a || "", item.n || 1, item.cur || 1].join("|");
      let bar = row.__vcBar;
      if (!bar || !bar.isConnected || bar.dataset.sig !== sig) {
        if (bar) bar.remove();
        bar = makeBar(row, turn, role, item, sig);
        row.__vcBar = bar;
        wrap.insertBefore(bar, row.nextSibling);
      }
      keep.add(bar);
    }
    // 行没了但工具栏还在（Gradio 重渲染只换行，不会管我们插进去的兄弟节点）
    // ★ 排除 .vc-confirm：删除的二次确认框带着 .vc-msgbar（要复用它那套布局样式），
    //   但它不是工具条、也不在 keep 里 —— 不排除的话会被这里当孤儿删掉，
    //   这时原工具条已被 display:none，表现为「点了删除却什么都没出现」。
    [...wrap.children].forEach((ch) => {
      if (ch.classList && ch.classList.contains("vc-msgbar")
          && !ch.classList.contains("vc-confirm") && !keep.has(ch)) ch.remove();
    });
  };
  window.__vcMsgBarSync = sync;

  // Chatbot 每次重渲染都会冲掉注入的按钮 -> 监听并补回来。
  // sync 幂等（已有工具条且签名没变就跳过），所以不会自己触发自己形成死循环。
  if (!window.__vcMsgObs) {
    let pending = false;
    window.__vcMsgObs = new MutationObserver(() => {
      if (pending) return;
      pending = true;
      setTimeout(() => { pending = false; sync(); }, 60);
    });
    window.__vcMsgObs.observe(document.body, { childList: true, subtree: true });
  }
  sync();
}

// 页面加载时把上面两个初始化跑起来（各自只跑一次，各自等自己的目标元素出现）。
// 为什么要等：这个脚本是 Gradio 运行时才插进 <head> 的，执行时组件可能还没渲染出来，
// 所以用 MutationObserver 盯着，等 #vc_sidebar / #vc_chatbox 出现再初始化，都跑完就断开。

// —— 对话自动滚到底 ——
// 用户要求：每生成一句都强制跟随到最新内容底部，绝不停在半路。
// 做法：盯住 #vc_chatbox 的任何内容变化（新消息 / 生成中的「……」/ 逐字增长 / 工具栏注入），
// 一变就把「里面所有可滚动的容器」全部滚到底（多处补滚，应对内容长高有延迟）。
// 同时对外提供 window.__vcScrollBottom()，供后端 _SCROLL_JS 直接调用。
function __vcInitAutoScroll() {
  if (window.__vcAutoScrollOn) return;
  window.__vcAutoScrollOn = true;

  const box = () => document.querySelector("#vc_chatbox");
  // 把 #vc_chatbox 内所有「内容超出高度」的容器都滚到底。
  // 不同 Gradio 版本真正的滚动容器类名不一样，这里一网打尽最稳。
  const toBottomAll = () => {
    const root = box();
    if (!root) return;
    for (const el of [root, ...root.querySelectorAll("*")]) {
      if (el.scrollHeight > el.clientHeight + 2) {
        el.scrollTop = el.scrollHeight;
      }
    }
  };
  window.__vcScrollBottom = toBottomAll;

  let pending = false;
  const kick = () => { toBottomAll(); };
  const obs = new MutationObserver(() => {
    if (pending) return;
    pending = true;
    // 分多拍补滚：内容（尤其逐字/流式）会持续长高，一次可能还没到最终高度。
    requestAnimationFrame(() => {
      pending = false;
      kick();
      setTimeout(kick, 30);
      setTimeout(kick, 120);
      setTimeout(kick, 300);
    });
  });
  const start = () => {
    const root = box();
    if (!root) return false;
    obs.observe(root, { childList: true, subtree: true, characterData: true });
    toBottomAll();
    return true;
  };
  if (!start()) {
    const w = new MutationObserver(() => { if (start()) w.disconnect(); });
    w.observe(document.documentElement, { childList: true, subtree: true });
  }
}

(() => {
  const jobs = [
    ["__vcSideInited", "#vc_sidebar", __vcInitSide],
    ["__vcMsgBarInited", "#vc_chatbox", __vcInitMsgBar],
    ["__vcAutoScrollInited", "#vc_chatbox", __vcInitAutoScroll],
  ];
  const boot = () => {
    jobs.forEach(([flag, sel, fn]) => {
      if (!window[flag] && document.querySelector(sel)) {
        window[flag] = true;
        fn();
      }
    });
    return jobs.every(([flag]) => window[flag]);
  };
  if (!boot()) {
    const obs = new MutationObserver(() => { if (boot()) obs.disconnect(); });
    obs.observe(document.documentElement, { childList: true, subtree: true });
  }
})();

/* API 接口设置弹窗：Esc / 点空白处 都能关掉（等于点里面的「取消」）。
   背景压暗与定位都是纯 CSS（见 ui.css 的 #vc_keymodal），这里只管关闭。 */
(() => {
  const SEL = "#vc_keymodal";
  const visible = (el) => {
    const st = window.getComputedStyle(el);
    if (st.display === "none" || st.visibility === "hidden" || Number(st.opacity) === 0) return false;
    // 注意：position:fixed 的元素 offsetParent 恒为 null，别用它判断可见性
    const r = el.getBoundingClientRect();
    return r.width > 2 && r.height > 2;
  };
  const openModal = () => {
    for (const el of document.querySelectorAll(SEL)) if (visible(el)) return el;
    return null;
  };
  const closeModal = (el) => {
    if (!el) return;
    const btn = [...el.querySelectorAll("button")]
      .find((b) => /关闭|取消/.test((b.textContent || "").trim()));
    if (btn) btn.click();
  };
  const boot = () => {
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") closeModal(openModal());
    });
    // 点在弹窗外面 = 关闭
    document.addEventListener("click", (e) => {
      const el = openModal();
      if (el && !el.contains(e.target)) closeModal(el);
    }, true);
  };
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();

/* 实时通话：/call 的 iframe 原来写死 height:640px，窗口一高下面就空出一大截。
   这里按「可视区高度 − iframe 顶部位置」实时算高度，切 tab / 改窗口大小都跟着变。 */
(() => {
  const PAD = 18;      // 底部留一点呼吸位，别贴着窗口边
  const MIN_H = 400;   // 再矮也留个能按到麦克风的高度
  const fitCall = () => {
    const f = document.querySelector('iframe[src="/call"]');
    if (!f || f.offsetWidth === 0) return;   // 没这个 iframe，或当前 tab 隐藏着，先跳过
    const top = f.getBoundingClientRect().top;
    let h = Math.max(MIN_H, window.innerHeight - top - PAD);
    f.style.setProperty("height", h + "px", "important");
    // 第二遍：万一整页超出视口（会出现滚动条，底下又空出来），把超出量扣掉
    const over = document.documentElement.scrollHeight - window.innerHeight;
    if (over > 2) {
      h = Math.max(MIN_H, h - over);
      f.style.setProperty("height", h + "px", "important");
    }
  };
  let tries = 0;
  const timer = setInterval(() => {
    fitCall();
    if (++tries > 60) clearInterval(timer);
  }, 250);
  window.addEventListener("resize", fitCall);
  // 切到「实时通话」标签、折叠/展开侧栏后都会改变可用高度
  document.addEventListener("click", () => setTimeout(fitCall, 80), true);
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", fitCall);
  } else {
    fitCall();
  }
})();

