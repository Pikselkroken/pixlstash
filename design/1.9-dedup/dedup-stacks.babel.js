const DS = window.PixlStashDesignSystem_ac544c || {};
const { Button, Kbd, SectionLabel, Badge, Tag, ScoreStars } = DS;
const { useState, useEffect, useCallback, useMemo } = React;

const KH = ({ keys }) => <span className="kbdhint">{keys.map((k, i) => <Kbd key={i}>{k}</Kbd>)}</span>;

const GROUPS = [
  { id:'g1', kind:'exact', conf:1, why:[['Identical file hash'],['Same dimensions'],['Imported 4 min apart']],
    cands:[
      { src:'scene-01', res:'6016×4016', px:24.2, size:14.8, date:'12 May 14:22', score:4, tags:2, fmt:'JPEG', path:'/shoots/may/DSC_4417.jpg' },
      { src:'scene-01', res:'6016×4016', px:24.2, size:14.8, date:'12 May 14:22', score:0, tags:0, fmt:'JPEG', path:'/backup/2024/DSC_4417 (1).jpg', ref:true },
    ] },
  { id:'g2', kind:'near', conf:0.96, why:[['96% visual match'],['Same capture second'],['Different resolution', true],['One is a re-export', true]],
    cands:[
      { src:'tile-03', res:'4032×3024', px:12.2, size:8.4, date:'03 Jun 09:11', score:3, tags:4, fmt:'JPEG', path:'/shoots/jun/IMG_2201.jpg' },
      { src:'tile-03', res:'1920×1440', px:2.8, size:1.1, date:'03 Jun 09:11', score:0, tags:0, fmt:'WEBP', path:'/export/web/IMG_2201.webp', ref:true },
      { src:'tile-03', res:'2048×1536', px:3.1, size:1.6, date:'11 Jun 18:40', score:0, tags:1, fmt:'JPEG', path:'/tmp/upscale/IMG_2201_x2.jpg', ref:true },
    ] },
  { id:'g3', kind:'near', conf:0.81, why:[['81% visual match'],['Burst — 0.6s apart'],['Same folder'],['Subject moved between frames', true]],
    cands:[
      { src:'tile-08', res:'4032×3024', px:12.2, size:7.9, date:'21 Jun 16:04', score:5, tags:3, fmt:'JPEG', path:'/shoots/jun/burst_0071.jpg' },
      { src:'tile-08', res:'4032×3024', px:12.2, size:8.1, date:'21 Jun 16:04', score:2, tags:0, fmt:'JPEG', path:'/shoots/jun/burst_0072.jpg' },
      { src:'tile-08', res:'4032×3024', px:12.2, size:7.6, date:'21 Jun 16:04', score:0, tags:0, fmt:'JPEG', path:'/shoots/jun/burst_0073.jpg' },
      { src:'tile-08', res:'4032×3024', px:12.2, size:8.3, date:'21 Jun 16:04', score:0, tags:0, fmt:'JPEG', path:'/shoots/jun/burst_0074.jpg' },
    ] },
  { id:'g4', kind:'near', conf:0.68, why:[['68% visual match'],['Same scene, different framing', true],['Different subject position', true]],
    cands:[
      { src:'scene-03', res:'5472×3648', px:20.0, size:11.2, date:'02 Jul 11:58', score:4, tags:5, fmt:'RAW', path:'/shoots/jul/A7R0912.arw' },
      { src:'scene-04', res:'5472×3648', px:20.0, size:10.7, date:'02 Jul 11:59', score:3, tags:2, fmt:'RAW', path:'/shoots/jul/A7R0913.arw' },
    ] },
  { id:'g5', kind:'exact', conf:1, why:[['Identical file hash'],['Same folder twice']],
    cands:[
      { src:'tile-11', res:'3000×2000', px:6.0, size:4.2, date:'18 Apr 08:30', score:3, tags:2, fmt:'JPEG', path:'/shoots/apr/set_a/0031.jpg' },
      { src:'tile-11', res:'3000×2000', px:6.0, size:4.2, date:'18 Apr 08:30', score:0, tags:0, fmt:'JPEG', path:'/shoots/apr/set_a copy/0031.jpg', ref:true },
    ] },
  { id:'g6', kind:'near', conf:0.93, why:[['93% visual match'],['Crop of the same frame'],['Different aspect ratio', true],['Different resolution', true]],
    cands:[
      { src:'tile-14', res:'6000×4000', px:24.0, size:13.4, date:'27 Apr 19:02', score:5, tags:6, fmt:'JPEG', path:'/shoots/apr/hero_full.jpg' },
      { src:'tile-14', res:'2400×2400', px:5.8, size:3.1, date:'27 Apr 19:44', score:2, tags:1, fmt:'JPEG', path:'/shoots/apr/hero_square.jpg' },
      { src:'tile-14', res:'1080×1080', px:1.2, size:0.6, date:'27 Apr 19:45', score:0, tags:0, fmt:'JPEG', path:'/export/social/hero_ig.jpg', ref:true },
    ] },
  { id:'g7', kind:'near', conf:0.87, why:[['87% visual match'],['Burst — 1.1s apart'],['Same folder'],['Eyes closed in one', true]],
    cands:[
      { src:'scene-05', res:'4032×3024', px:12.2, size:8.8, date:'09 Jul 07:15', score:4, tags:2, fmt:'JPEG', path:'/shoots/jul/dawn_014.jpg' },
      { src:'scene-05', res:'4032×3024', px:12.2, size:8.6, date:'09 Jul 07:15', score:0, tags:0, fmt:'JPEG', path:'/shoots/jul/dawn_015.jpg' },
      { src:'scene-05', res:'4032×3024', px:12.2, size:9.0, date:'09 Jul 07:15', score:0, tags:0, fmt:'JPEG', path:'/shoots/jul/dawn_016.jpg' },
    ] },
];

// keeper score — largest pixel count, then metadata richness, then user score
function pickCover(cands) {
  let best = 0;
  cands.forEach((c, i) => {
    const s = c.px * 4 + c.tags * 3 + c.score * 2 + (c.fmt === 'RAW' ? 8 : 0);
    const bs = cands[best].px * 4 + cands[best].tags * 3 + cands[best].score * 2 + (cands[best].fmt === 'RAW' ? 8 : 0);
    if (s > bs) best = i;
  });
  return best;
}
const bestOf = (cands, key) => Math.max(...cands.map(c => c[key]));
// keep the filename visible without bidi reordering — truncate the head in JS, not with direction:rtl
const shortPath = (p) => { const s = p.split('/').filter(Boolean); return s.length > 2 ? '…/' + s.slice(-2).join('/') : p; };

function Shell({ children, active, crumb }) {
  return (
    <React.Fragment>
      <div className="titlebar">
        <div className="tb-brand"><img src="../../assets/Logo.png" alt="" /><span className="wordmark">Pixl<span className="s">Stash</span></span></div>
        <nav className="breadcrumb"><span className="sep">›</span><span className="crumb link">Global</span><span className="sep">›</span><span className="crumb">{crumb}</span></nav>
        <span className="tb-spacer"></span><span className="tb-version">v1.9.0-dev.3</span>
        <div className="tb-win">
          <button><span className="mdi mdi-window-minimize"></span></button>
          <button><span className="mdi mdi-window-maximize"></span></button>
          <button className="close"><span className="mdi mdi-window-close"></span></button>
        </div>
      </div>
      <div className="fm">
        <aside className="sidebar">
          <div className="side-tabs">
            <button className="side-tab active"><span className="mdi mdi-web"></span> Global</button>
            <button className="side-tab"><span className="mdi mdi-folder-outline"></span> Projects</button>
            <button className="side-tab"><span className="mdi mdi-monitor"></span> Folders</button>
          </div>
          <div className="side-scroll">
            <div className={`row ${active === 'all' ? 'active' : ''}`}><span className="lead mdi mdi-image-multiple"></span><span className="label">All Pictures</span><span className="count">128,412</span></div>
            <div className={`row ${active === 'dupes' ? 'active' : ''}`}><span className="lead mdi mdi-content-duplicate"></span><span className="label">Duplicates</span>{active === 'dupes' ? children.badge : <span className="count">4</span>}</div>
            <div className="row"><span className="lead mdi mdi-trash-can-outline"></span><span className="label">Scrapheap</span><span className="count">2</span></div>
            <div className="sec">People<span className="sp"></span><span className="mdi mdi-plus"></span></div>
            <div className="row"><img className="avatar" src="../../assets/samples/tile-01.webp" alt="" /><span className="label">Angela Merkel</span><span className="count">8</span></div>
            <div className="row"><img className="avatar" src="../../assets/samples/tile-05.webp" alt="" /><span className="label">Walter</span><span className="count">4</span></div>
            <div className="sec">Sets<span className="sp"></span><span className="mdi mdi-plus"></span></div>
            <div className="row"><span className="set-ico mdi mdi-crown" style={{ color:'#00acc1' }}></span><span className="label">Celebrities</span><span className="count">20</span></div>
          </div>
        </aside>
        <main className="main">{children.main}</main>
      </div>
    </React.Fragment>
  );
}

function Cand({ c, i, cands, isCover, isOut, onCover, onToggle }) {
  return (
    <div className={`cand ${isCover ? 'cover' : ''} ${isOut ? 'out' : ''}`}
      onClick={() => onCover(i)}
      onContextMenu={(e) => { e.preventDefault(); onToggle(i); }}
      title={isOut ? 'Right-click to include in the stack' : 'Click to make cover · right-click to leave out of the stack'}>
      <div className="cthumb">
        <img src={`../../assets/samples/${c.src}.webp`} alt="" />
        {isCover && !isOut && <span className="cflag"><span className="mdi mdi-star" style={{ fontSize:12 }}></span>Cover</span>}
        {isOut && <span className="cflag outf">Not in stack</span>}
        <span className="cnum">{i + 1}</span>
      </div>
      <div className="cmeta">
        <div className="mline"><span className="mk">Resolution</span><span className={`mv ${c.px === bestOf(cands,'px') ? 'best' : 'dim'}`}>{c.res}</span></div>
        <div className="mline"><span className="mk">File</span><span className={`mv ${c.size === bestOf(cands,'size') ? 'best' : 'dim'}`}>{c.size} MB · {c.fmt}</span></div>
        <div className="mline"><span className="mk">Captured</span><span className="mv dim">{c.date}</span></div>
        <div className="mline"><span className="mk">Score</span><span className={`mv ${c.score && c.score === bestOf(cands,'score') ? 'best' : 'dim'}`}>{c.score ? '★'.repeat(c.score) : '—'}</span></div>
        <div className="mline"><span className="mk">Metadata</span><span className={`mv ${c.tags && c.tags === bestOf(cands,'tags') ? 'best' : 'dim'}`}>{c.tags ? `${c.tags} tags` : 'none'}</span></div>
        <div className="mline"><span className="mk">In stack</span>
          <span className="mv" style={{ display:'flex', alignItems:'center', gap:4 }}>
            <button className="bbtn bbtn--icon" style={{ height:18, width:18 }} title={isOut ? 'Include in stack' : 'Leave out of stack'}
              onClick={(e) => { e.stopPropagation(); onToggle(i); }}>
              <span className={`mdi mdi-${isOut ? 'plus-circle-outline' : 'minus-circle-outline'}`} style={{ fontSize:15 }}></span>
            </button>
            <span style={{ color:'var(--text-muted)' }}>{isOut ? 'No' : 'Yes'}</span>
          </span>
        </div>
      </div>
      {c.ref && (
        <div className="cfoot">
          <span className="mdi mdi-folder-eye-outline" title="Reference folder — not managed by PixlStash"></span>
          <span style={{ flex:1, minWidth:0, whiteSpace:'nowrap', overflow:'hidden', textOverflow:'ellipsis' }} title={c.path}>{shortPath(c.path)}</span>
        </div>
      )}
    </div>
  );
}

function GroupRow({ g, n, focused, cover, out, onFocus, onCover, onToggle, onStack, onSeparate, onCompare }) {
  const inStack = g.cands.length - out.length;
  return (
    <div className={`grow ${focused ? 'focus' : ''}`} onClick={onFocus}>
      <div className="ginfo">
        {focused && <span className="gcaret mdi mdi-menu-right"></span>}
        <div className="gn"><b>Group {n}</b><span>{g.cands.length} pictures</span></div>
        <span className={`conf ${g.kind === 'exact' ? 'exact' : ''}`} style={{ alignSelf:'flex-start' }}>
          <span className={`mdi mdi-${g.kind === 'exact' ? 'approximately-equal' : 'blur'}`} style={{ fontSize:14 }}></span>
          {g.kind === 'exact' ? <b>Exact</b> : <React.Fragment><b>{Math.round(g.conf * 100)}%</b> similar</React.Fragment>}
        </span>
        {focused
          ? <span className="gfocusnote"><span className="mdi mdi-keyboard-outline" style={{ fontSize:13 }}></span>Keyboard acts here</span>
          : <div className="greasons">{[...g.why.filter(w => w[1]), ...g.why.filter(w => !w[1])].slice(0, 2).map(([w, neg], i) => <i key={i} className={neg ? 'neg' : ''}><span className={`mdi mdi-${neg ? 'close' : 'check'}`}></span>{w}</i>)}</div>}
      </div>
      <div className="gstrip">
        {g.cands.map((c, i) => {
          const isOut = out.includes(i);
          return (
            <div key={i} className={`gthumb ${i === cover ? 'iscover' : ''} ${isOut ? 'out' : ''}`}
              onClick={(e) => { e.stopPropagation(); onFocus(); onCover(i); }}
              onContextMenu={(e) => { e.preventDefault(); onFocus(); onToggle(i); }}
              title={isOut ? 'Right-click to include' : 'Click to make cover · right-click to exclude'}>
              <div className="gt">
                <img src={`../../assets/samples/${c.src}.webp`} alt="" />
                <span className="gnum">{i + 1}</span>
                {i === cover && !isOut && <span className="gcv">COVER</span>}
                {isOut && <span className="gx mdi mdi-minus-circle-outline"></span>}
              </div>
            </div>
          );
        })}
      </div>
      <div className="gact">
        <Button variant={focused ? 'accent' : 'secondary'} size="sm" iconLeft="layers-plus" onClick={(e) => { e.stopPropagation(); onStack(); }}>Stack {inStack}{focused && <span className="kin"><Kbd>Enter</Kbd></span>}</Button>
        <Button variant="ghost" size="sm" iconLeft="call-split" onClick={(e) => { e.stopPropagation(); onSeparate(); }}>Keep separate{focused && <span className="kin"><Kbd>S</Kbd></span>}</Button>
        <button className="gcompare" onClick={(e) => { e.stopPropagation(); onFocus(); onCompare(); }}>
          <span className="mdi mdi-compare-horizontal" style={{ fontSize:15 }}></span>Compare all {g.cands.length}{focused && <Kbd>C</Kbd>}
        </button>
      </div>
    </div>
  );
}

function Queue() {
  const [focus, setFocus] = useState(0);
  const [covers, setCovers] = useState(() => GROUPS.map(g => pickCover(g.cands)));
  const [outs, setOuts] = useState(() => GROUPS.map(() => []));
  const [resolved, setResolved] = useState([]);
  const [receipt, setReceipt] = useState(null);
  const [compare, setCompare] = useState(null);
  const listRef = React.useRef(null);
  const [scan, setScan] = useState(62);
  const [tiersOpen, setTiersOpen] = useState(false);
  const [tiers, setTiers] = useState({ high:true, medium:true, loose:false });
  const tierLabel = !tiers.high ? 'Exact only' : tiers.loose ? 'Exact + all near' : tiers.medium ? 'Exact + near ≥75%' : 'Exact + near ≥90%';
  const inTier = (g) => g.kind === 'exact' || (g.conf >= 0.90 ? tiers.high : g.conf >= 0.75 ? tiers.medium : tiers.loose);
  const open = GROUPS.map((g, i) => i).filter(i => !resolved.some(r => r.i === i) && inTier(GROUPS[i]));
  const focusIdx = open.includes(focus) ? focus : (open[0] ?? -1);

  useEffect(() => {
    const t = setInterval(() => setScan(s => (s >= 100 ? 100 : s + 1)), 900);
    return () => clearInterval(t);
  }, []);

  const resolve = useCallback((i, verdict) => {
    const g = GROUPS[i];
    if (!g) return;
    const n = g.cands.length - outs[i].length;
    setResolved(r => [...r, { i, verdict }]);
    setReceipt({ verdict, n, at: Date.now() });
    setFocus(f => {
      const rest = GROUPS.map((_, j) => j).filter(j => j !== i && !resolved.some(r => r.i === j));
      return rest.find(j => j > i) ?? rest[rest.length - 1] ?? 0;
    });
  }, [outs, resolved]);

  useEffect(() => { if (!receipt) return; const t = setTimeout(() => setReceipt(null), 5000); return () => clearTimeout(t); }, [receipt]);

  const undo = useCallback(() => {
    setResolved(r => {
      if (!r.length) return r;
      setFocus(r[r.length - 1].i);
      setReceipt(null);
      return r.slice(0, -1);
    });
  }, []);

  useEffect(() => {
    const list = listRef.current;
    if (!list || focusIdx < 0) return;
    const row = list.querySelector('.grow.focus');
    if (!row) return;
    const top = row.offsetTop, bottom = top + row.offsetHeight;
    if (top < list.scrollTop) list.scrollTop = top;
    else if (bottom > list.scrollTop + list.clientHeight) list.scrollTop = bottom - list.clientHeight;
  }, [focusIdx, resolved.length]);

  const setCover = (i, v) => setCovers(c => c.map((x, j) => j === i ? v : x));
  const toggleOut = (i, v) => setOuts(o => o.map((x, j) => j === i ? (x.includes(v) ? x.filter(y => y !== v) : [...x, v]) : x));

  useEffect(() => {
    const h = (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z') { e.preventDefault(); undo(); return; }
      if (e.key === 'Escape' && compare !== null) { setCompare(null); return; }
      if (e.ctrlKey || e.metaKey || focusIdx < 0) return;
      const k = e.key.toLowerCase();
      if (compare !== null) {
        if (k === 'enter') { e.preventDefault(); resolve(compare, 'stacked'); setCompare(null); }
        else if (k === 's') { e.preventDefault(); resolve(compare, 'separate'); setCompare(null); }
        return;
      }
      if (k === 'enter') { e.preventDefault(); resolve(focusIdx, 'stacked'); }
      else if (k === 's') { e.preventDefault(); resolve(focusIdx, 'separate'); }
      else if (k === 'arrowdown' || k === 'j') { e.preventDefault(); const p = open.indexOf(focusIdx); setFocus(open[Math.min(open.length - 1, p + 1)]); }
      else if (k === 'arrowup' || k === 'k') { e.preventDefault(); const p = open.indexOf(focusIdx); setFocus(open[Math.max(0, p - 1)]); }
      else if (k === 'c') { e.preventDefault(); setCompare(focusIdx); }
      else if (/^[1-9]$/.test(k)) { const n = +k - 1; if (n < GROUPS[focusIdx].cands.length) setCover(focusIdx, n); }
      else if (k === 'x') { toggleOut(focusIdx, covers[focusIdx]); }
    };
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  }, [focusIdx, open, covers, compare, resolve, undo]);

  return (
    <div className="frame">
      <div className="app" style={{ height:700 }}>
        <Shell crumb="Duplicates" active="dupes" children={{
          badge: <span className="badge">{open.length}</span>,
          main: <React.Fragment>
            <div className="toolbar">
              <div className="tb-left">
                <div className="split">
                  <button className="bbtn bbtn--icon"><span className="mdi mdi-sort-ascending"></span></button>
                  <button className="bbtn"><span className="prefix">Sort:</span><span className="mdi mdi-star"></span><span>Confidence</span><span className="mdi mdi-menu-down chev"></span></button>
                </div>
                <button className={`bbtn ${tiersOpen ? 'bbtn--open' : ''}`} onClick={() => setTiersOpen(t => !t)}><span className="mdi mdi-filter-outline"></span><span>{tierLabel}</span><span className="mdi mdi-menu-down chev"></span></button>
                <button className="bbtn"><span className="prefix">Scope:</span><span>Whole library</span><span className="mdi mdi-menu-down chev"></span></button>
                <div className="sep"></div>
                <div className="grp">
                  <button className="bbtn bbtn--icon" onClick={undo} title="Undo" style={{ opacity: resolved.length ? 1 : .35 }}><span className="mdi mdi-undo-variant"></span></button>
                  <button className="bbtn bbtn--icon" title="Redo" style={{ opacity:.35 }}><span className="mdi mdi-redo-variant"></span></button>
                </div>
              </div>
              <div className="tb-right">
                <button className="bbtn bbtn--accent"><span className="mdi mdi-flash-outline"></span>Auto-stack 1,204 exact matches</button>
                <div className="sep"></div>
                <button className="bbtn bbtn--icon" title="Duplicate detection settings"><span className="mdi mdi-cog-outline"></span></button>
              </div>
            </div>
            {tiersOpen && (
              <div className="tbm" style={{ width:340, top:42, left:150 }}>
                <span className="tbm-caret"></span>
                <div className="tbm-head"><span className="ic mdi mdi-filter"></span><span className="tbm-title">Include</span><span className="tbm-sp" style={{ flex:1 }}></span>
                  <span className="tbm-count">{open.length} groups</span></div>
                <div className="tbm-sec">
                  <div className="tierrow locked">
                    <span className="cbox"><span className="mdi mdi-check"></span></span>
                    <span className="tname">Exact matches <span className="trange">hash</span></span>
                    <span className="tcount">1,204</span>
                  </div>
                  <div className={`tierrow ${tiers.high ? 'on' : ''}`} onClick={() => setTiers(t => ({ ...t, high:!t.high, medium:t.high ? false : t.medium, loose:t.high ? false : t.loose }))}>
                    <span className="cbox">{tiers.high && <span className="mdi mdi-check"></span>}</span>
                    <span className="tname">Near-identical <span className="trange">≥90%</span></span>
                    <span className="tcount">96</span>
                  </div>
                  <div className={`tierrow ${tiers.medium ? 'on' : ''}`} style={{ opacity: tiers.high ? 1 : .4, pointerEvents: tiers.high ? 'auto' : 'none' }}
                    onClick={() => setTiers(t => ({ ...t, medium:!t.medium, loose:t.medium ? false : t.loose }))}>
                    <span className="cbox">{tiers.medium && <span className="mdi mdi-check"></span>}</span>
                    <span className="tname">Bursts &amp; re-exports <span className="trange">75–90%</span></span>
                    <span className="tcount">38</span>
                  </div>
                  <div className={`tierrow ${tiers.loose ? 'on' : ''}`} style={{ opacity: tiers.medium ? 1 : .4, pointerEvents: tiers.medium ? 'auto' : 'none' }}
                    onClick={() => setTiers(t => ({ ...t, loose:!t.loose }))}>
                    <span className="cbox">{tiers.loose && <span className="mdi mdi-check"></span>}</span>
                    <span className="tname">Same scene <span className="trange">65–75%</span></span>
                    <span className="tcount">9</span>
                  </div>
                  {tiers.loose && (
                    <div className="tierwarn"><span className="mdi mdi-alert-outline"></span>
                      Same-scene groups are often genuinely different pictures. Compare before stacking — these are the ones people get wrong.</div>
                  )}
                </div>
              </div>
            )}
            {scan < 100 && (
              <div className="scanbar">
                <span className="sb-ico mdi mdi-radar"></span>
                <span className="sb-txt">Still scanning — <b>{(scan * 1284).toLocaleString()}</b> of <b>128,412</b> pictures compared. Groups appear as they're found.</span>
                <span className="sb-sub">{scan}% · ~{Math.max(1, Math.round((100 - scan) / 7))} min left</span>
                <div className="sb-track"><div className="sb-fill" style={{ width:`${scan}%` }}></div></div>
              </div>
            )}
            {focusIdx >= 0 ? (
              <div className="queue">
                <div className="qhead">
                  <span className="qtitle">{open.length} groups to review</span>
                  <span className="qsub">{resolved.length} done</span>
                  <span className="sp"></span>
                  <span className="khint" style={{ color:'var(--text)' }}><KH keys={['↑','↓']} /> choose group</span>
                  <span className="khint"><KH keys={['Enter']} /> stack it</span>
                  <span className="khint"><KH keys={['S']} /> keep separate</span>
                  <span className="khint"><KH keys={['C']} /> compare</span>
                  <span className="khint"><KH keys={['Ctrl','Z']} /> undo</span>
                </div>
                <div className="qlist" ref={listRef}>
                  {open.map(i => (
                    <GroupRow key={GROUPS[i].id} g={GROUPS[i]} n={i + 1} focused={i === focusIdx}
                      cover={covers[i]} out={outs[i]} onFocus={() => setFocus(i)}
                      onCover={(v) => setCover(i, v)} onToggle={(v) => toggleOut(i, v)}
                      onStack={() => resolve(i, 'stacked')} onSeparate={() => resolve(i, 'separate')}
                      onCompare={() => setCompare(i)} />
                  ))}
                </div>
              </div>
            ) : (
              <div className="qdone">
                <span className="mdi mdi-check-circle-outline"></span>
                <h3>Queue clear</h3>
                <p>{resolved.filter(r => r.verdict === 'stacked').length} groups stacked, {resolved.filter(r => r.verdict === 'separate').length} kept separate. Scanning continues in the background — new groups will appear here as they're found.</p>
                <Button variant="secondary" size="sm" iconLeft="undo-variant" onClick={undo}>Undo last</Button>
              </div>
            )}
            {compare !== null && GROUPS[compare] && (
              <div className="scrim" onClick={() => setCompare(null)}>
                <div className="dlg" style={{ width:'auto', maxWidth:1180 }} onClick={(e) => e.stopPropagation()}>
                  <div className="dlg-head"><span className="mdi mdi-compare-horizontal"></span><b>Compare group {compare + 1}</b>
                    <span style={{ flex:1 }}></span>
                    <span className={`conf ${GROUPS[compare].kind === 'exact' ? 'exact' : ''}`}>
                      {GROUPS[compare].kind === 'exact' ? <b>Exact match</b> : <React.Fragment><b>{Math.round(GROUPS[compare].conf * 100)}%</b> similar</React.Fragment>}
                    </span>
                  </div>
                  <div className="dlg-body">
                    <div className="cands" style={{ padding:0 }}>
                      {GROUPS[compare].cands.map((c, i) => (
                        <Cand key={i} c={c} i={i} cands={GROUPS[compare].cands} isCover={i === covers[compare]}
                          isOut={outs[compare].includes(i)} onCover={(v) => setCover(compare, v)} onToggle={(v) => toggleOut(compare, v)} />
                      ))}
                    </div>
                    <div className="why" style={{ padding:0 }}>
                      {GROUPS[compare].why.map(([w, neg], i) => (
                        <span key={i} className={`wtag ${neg ? 'wtag--neg' : ''}`}><span className={`mdi mdi-${neg ? 'close' : 'check'}`}></span>{w}</span>
                      ))}
                    </div>
                  </div>
                  <div className="dlg-foot">
                    <span className="khint" style={{ marginRight:'auto' }}>Click a picture to make it cover · right-click to leave it out</span>
                    <Button variant="ghost" size="md" onClick={() => setCompare(null)}>Close<span className="kin"><Kbd>Esc</Kbd></span></Button>
                    <Button variant="secondary" size="md" iconLeft="call-split" onClick={() => { resolve(compare, 'separate'); setCompare(null); }}>Keep separate<span className="kin"><Kbd>S</Kbd></span></Button>
                    <Button variant="accent" size="md" iconLeft="layers-plus" onClick={() => { resolve(compare, 'stacked'); setCompare(null); }}>Stack {GROUPS[compare].cands.length - outs[compare].length}<span className="kin"><Kbd>Enter</Kbd></span></Button>
                  </div>
                </div>
              </div>
            )}
            {receipt && (
              <div className="receipt">
                <span className={`r-ico mdi mdi-${receipt.verdict === 'stacked' ? 'layers-plus' : 'call-split'}`}></span>
                <span className="r-text">{receipt.verdict === 'stacked'
                  ? <React.Fragment>Stacked <b>{receipt.n} pictures</b> — cover kept, nothing deleted</React.Fragment>
                  : <React.Fragment>Kept <b>{receipt.n} pictures</b> separate — won't be suggested again</React.Fragment>}</span>
                <Button variant="ghost" size="sm" iconLeft="undo-variant" onClick={undo}>Undo<span className="kin"><Kbd>Ctrl</Kbd><Kbd>Z</Kbd></span></Button>
                <span className="r-progress"></span>
              </div>
            )}
          </React.Fragment>
        }} />
      </div>
    </div>
  );
}

const GRID = ['tile-06','tile-04','tile-07','tile-09','tile-10','tile-11','tile-12','tile-13','tile-14','tile-15','tile-16','tile-01'];
const STACK_AT = 2;
const STACK_MEMBERS = ['tile-08','tile-08','tile-08','tile-08'];

function GridStacks() {
  const [expanded, setExpanded] = useState(false);
  const [menu, setMenu] = useState('filter');
  const [stackFilter, setStackFilter] = useState('all');
  return (
    <div className="frame">
      <div className="app" style={{ height:660 }}>
        <Shell crumb="All Pictures" active="all" children={{ main: <React.Fragment>
          <div className="toolbar">
            <div className="tb-left">
              <div className="split">
                <button className="bbtn bbtn--icon"><span className="mdi mdi-sort-ascending"></span></button>
                <button className="bbtn"><span className="prefix">Sort:</span><span className="mdi mdi-calendar"></span><span>Date Created</span><span className="mdi mdi-menu-down chev"></span></button>
              </div>
              <button className={`bbtn ${menu === 'filter' ? 'bbtn--open' : ''}`} onClick={() => setMenu(m => m === 'filter' ? null : 'filter')}><span className="mdi mdi-filter-outline"></span><span className="mdi mdi-menu-down chev"></span></button>
              <button className={`bbtn ${menu === 'view' ? 'bbtn--open' : ''}`} onClick={() => setMenu(m => m === 'view' ? null : 'view')} title="Grid view"><span className="mdi mdi-view-grid"></span><span className="mdi mdi-menu-down chev"></span></button>
              <div className="sep"></div>
              <button className="bbtn bbtn--icon" title="Search (F)"><span className="mdi mdi-magnify"></span></button>
              <button className="bbtn bbtn--icon" title="Export current grid to zip"><span className="mdi mdi-tray-arrow-down"></span></button>
              <button className="bbtn bbtn--icon" title="Import photos"><span className="mdi mdi-cloud-upload-outline"></span></button>
            </div>
            <div className="tb-right">
              <button className="bbtn bbtn--icon" title="Review and fix tags"><span className="mdi mdi-tag-check-outline"></span></button>
              <button className="bbtn bbtn--icon" title="Settings"><span className="mdi mdi-cog-outline"></span></button>
              <button className="bbtn bbtn--icon" title="Show/Hide stats sidebar"><span className="mdi mdi-chart-bar"></span></button>
            </div>
          </div>
        <div className="grid-scroll">
          {menu === 'filter' && (
            <div className="tbm" style={{ width:376, top:6, left:104 }}>
              <span className="tbm-caret"></span>
              <div className="rfhead"><span className="ic mdi mdi-filter"></span><span className="t">Filters</span><span className="sp"></span>
                <span className="cnt">{stackFilter === 'dupes' ? '9' : stackFilter === 'only' ? '61' : '128'} matches</span>
                <button className="clear"><span className="mdi mdi-close-circle-outline" style={{ fontSize:16 }}></span> Clear</button></div>
              <div className="rfsec">
                <div className="rfcheckrow">
                  <label className="rfcheck"><span className="box"></span>Shared pictures only</label>
                  <label className="rfcheck"><span className="box"></span>Unassigned only</label>
                </div>
              </div>
              <div className="rfsec">
                <div style={{ display:'flex', gap:'var(--space-4)' }}>
                  <div style={{ flex:1 }}>
                    <span className="rflabel">Media</span>
                    <div className="bigseg" style={{ marginTop:'var(--space-2)' }}>
                      <button className="bigbtn bigbtn--on" title="All media"><span className="mdi mdi-image-multiple-outline"></span></button>
                      <button className="bigbtn" title="Images"><span className="mdi mdi-image-outline"></span></button>
                      <button className="bigbtn" title="Video"><span className="mdi mdi-video-outline"></span></button>
                    </div>
                  </div>
                  <div style={{ flex:1, display:'flex', flexDirection:'column', alignItems:'flex-end' }}>
                    <span className="rflabel">Faces</span>
                    <div className="bigseg" style={{ marginTop:'var(--space-2)' }}>
                      <button className="bigbtn bigbtn--on" title="Any"><span className="mdi mdi-infinity"></span></button>
                      <button className="bigbtn" title="Has face"><span className="mdi mdi-face-recognition"></span></button>
                      <button className="bigbtn" title="No face"><span className="mdi mdi-account-off-outline"></span></button>
                    </div>
                  </div>
                </div>
                <div style={{ marginTop:'var(--space-3)' }}>
                  <span className="rflabel">Stacks</span>
                  <div className="bigseg" style={{ marginTop:'var(--space-2)', alignSelf:'flex-start', width:'fit-content' }}>
                    {[['all','Any stack state','infinity'],['only','Stacked only','image-multiple'],['unstacked','Unstacked only','image-outline'],['dupes','Unresolved duplicates','content-duplicate']].map(([v, label, ic]) => (
                      <button key={v} className={`bigbtn ${stackFilter === v ? 'bigbtn--on' : ''}`} title={label} onClick={() => setStackFilter(v)}>
                        <span className={`mdi mdi-${ic}`}></span>
                      </button>
                    ))}
                  </div>
                </div>
              </div>
              <div className="rfsec">
                <div style={{ display:'flex', justifyContent:'space-between' }}><span className="rflabel">Min score</span><span className="rflabel">Max score</span></div>
                <div style={{ display:'flex', justifyContent:'space-between' }}>
                  <div className="rfstars">{[0,1,2,3,4].map(i => <span key={i} className="mdi mdi-star-outline"></span>)}</div>
                  <div className="rfstars">{[0,1,2,3,4].map(i => <span key={i} className="mdi mdi-star-outline"></span>)}</div>
                </div>
              </div>
              <div className="rfsec"><div className="rfcollapse"><span className="rflabel">Impossible tags</span><span className="mdi mdi-chevron-down"></span></div></div>
              <div className="rfsec">
                <div className="rfcollapse" style={{ cursor:'default' }}><span className="rflabel">Tags</span><span style={{ fontSize:'var(--text-sm)', color:'var(--text-muted)' }}>Clear</span></div>
                <div className="rfinput"><span className="mdi mdi-magnify" style={{ fontSize:16 }}></span> Filter by tag…</div>
              </div>
              <div className="rfsec"><div className="rfcollapse"><span className="rflabel">Tag confidence</span><span className="mdi mdi-chevron-down"></span></div></div>
              <div className="rfsec"><div className="rfcollapse"><span className="rflabel">ComfyUI</span><span className="mdi mdi-chevron-down"></span></div></div>
            </div>
          )}
          {menu === 'view' && (
            <div className="tbm" style={{ width:264, top:6, left:150 }}>
              <span className="tbm-caret"></span>
              <div className="tbm-head"><span className="ic mdi mdi-view-grid"></span><span className="tbm-title">Grid view</span><span className="tbm-sp"></span>
                <button className="tbm-ghost"><span className="mdi mdi-view-compact-outline"></span> Compact</button></div>
              <div className="tbm-sec">
                <div className="row-between"><span>Size</span><div className="slider"><span className="fill"></span><span className="knob"></span></div><span className="mono">Medium</span></div>
              </div>
              <div className="tbm-sec">
                <span className="tbm-label">Stacks</span>
                <div className="btngroup">
                  <button className={`gbtn ${expanded ? 'gbtn--on' : ''}`} onClick={() => setExpanded(true)}><span className="mdi mdi-arrow-expand-vertical"></span>Expand all</button>
                  <button className={`gbtn ${!expanded ? 'gbtn--on' : ''}`} onClick={() => setExpanded(false)}><span className="mdi mdi-arrow-collapse-vertical"></span>Collapse</button>
                </div>
              </div>
              <div className="tbm-sec">
                <span className="tbm-label">Overlays</span>
                <div className="grid3">
                  <button className="toggle toggle--vert"><span className="mdi mdi-face-recognition"></span>Face boxes</button>
                  <button className="toggle toggle--vert on"><span className="mdi mdi-shape-outline"></span>Object boxes</button>
                  <button className="toggle toggle--vert on"><span className="mdi mdi-alert-outline"></span>Problems</button>
                </div>
              </div>
            </div>
          )}
          <div className="cgrid">
            {GRID.slice(0, STACK_AT).map((t, i) => (
              <div key={i} className="cell"><img src={`../../assets/samples/${t}.webp`} alt="" /></div>
            ))}
            <div className="cell stack">
              <img src="../../assets/samples/tile-08.webp" alt="" />
              <span className="stackbadge"><span className="mdi mdi-image-multiple"></span>4</span>
              <span className="score-dot"></span>
            </div>
            {expanded && (
              <div className="exp">
                <div className="ehead">
                  <b><span className="mdi mdi-image-multiple"></span>Stack of 4</b>
                  <span>Burst · 81% similar</span>
                  <span>21 Jun 16:04</span>
                </div>
                <div className="estrip">
                  {STACK_MEMBERS.map((m, i) => (
                    <div key={i} className={`eth ${i === 0 ? 'iscover' : ''}`}>
                      <img src={`../../assets/samples/${m}.webp`} alt="" />
                      {i === 0 && <span className="cv">Cover</span>}
                    </div>
                  ))}
                  <div style={{ display:'flex', alignItems:'center', paddingLeft:'var(--space-2)', gap:'var(--space-2)' }}>
                    <Button variant="ghost" size="sm" iconLeft="call-split">Unstack</Button>
                    <Button variant="ghost" size="sm" iconLeft="star-outline">Set cover</Button>
                  </div>
                </div>
              </div>
            )}
            {GRID.slice(STACK_AT).map((t, i) => (
              <div key={i} className="cell"><img src={`../../assets/samples/${t}.webp`} alt="" /></div>
            ))}
          </div>
        </div>
        </React.Fragment> }} />
      </div>
    </div>
  );
}

function AutoStackDialog() {
  return (
    <div className="frame" style={{ position:'relative' }}>
      <div className="app" style={{ height:420 }}>
        <div className="grid-scroll" style={{ filter:'blur(1px)', opacity:.5 }}>
          <div className="cgrid">
            {GRID.map((t, i) => <div key={i} className="cell"><img src={`../../assets/samples/${t}.webp`} alt="" /></div>)}
          </div>
        </div>
        <div className="scrim">
          <div className="dlg">
            <div className="dlg-head"><span className="mdi mdi-flash-outline"></span><b>Auto-stack exact matches</b></div>
            <div className="dlg-body">
              <p>Byte-identical files need no judgment. This stacks every exact-match group and picks the copy with the richest metadata as cover. Near-duplicates are left for the queue.</p>
              <div className="dryrun">
                <div className="dr pos"><span className="mdi mdi-layers-plus"></span><span className="sp">Stacks to create</span><b>1,204</b></div>
                <div className="dr pos"><span className="mdi mdi-image-multiple-outline"></span><span className="sp">Pictures collapsed into a cover</span><b>2,861</b></div>
                <div className="dr"><span className="mdi mdi-tag-multiple-outline"></span><span className="sp">Covers gaining metadata from copies</span><b>318</b></div>
                <div className="dr"><span className="mdi mdi-blur"></span><span className="sp">Near-duplicate groups left in queue</span><b>143</b></div>
                <div className="dr"><span className="mdi mdi-delete-off-outline"></span><span className="sp">Files deleted</span><b>0</b></div>
              </div>
              <p style={{ display:'flex', alignItems:'center', gap:'var(--space-2)' }}><span className="mdi mdi-information-outline" style={{ fontSize:16, color:'var(--accent)' }}></span>Reversible as one step with <Kbd>Ctrl</Kbd><Kbd>Z</Kbd> for the rest of the session.</p>
            </div>
            <div className="dlg-foot">
              <span className="sp"></span>
              <Button variant="ghost" size="md">Cancel</Button>
              <Button variant="accent" size="md" iconLeft="layers-plus">Create 1,204 stacks<span className="kin"><Kbd>Enter</Kbd></span></Button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function ContextEntry() {
  const [open, setOpen] = useState(true);
  return (
    <div className="frame">
      <div className="app" style={{ height:420 }}>
        <div className="fm">
          <aside className="sidebar" style={{ position:'relative' }}>
            <div className="side-tabs">
              <button className="side-tab active"><span className="mdi mdi-web"></span> Global</button>
              <button className="side-tab"><span className="mdi mdi-folder-outline"></span> Projects</button>
              <button className="side-tab"><span className="mdi mdi-monitor"></span> Folders</button>
            </div>
            <div className="side-scroll">
              <div className="row"><span className="lead mdi mdi-image-multiple"></span><span className="label">All Pictures</span><span className="count">128,412</span></div>
              <div className="row"><span className="lead mdi mdi-content-duplicate"></span><span className="label">Duplicates</span><span className="count">143</span></div>
              <div className="sec">Sets<span className="sp"></span><span className="mdi mdi-plus"></span></div>              <div className="row"><span className="set-ico mdi mdi-crown" style={{ color:'#00acc1' }}></span><span className="label">Celebrities</span><span className="count">20</span></div>
              <div className={`row ${open ? 'active' : ''}`} onClick={() => setOpen(o => !o)} style={{ cursor:'context-menu' }}>
                <span className="set-ico mdi mdi-folder-multiple-image" style={{ color:'#26a69a' }}></span><span className="label">Release Set B</span><span className="count">1,482</span>
              </div>
              <div className="row"><span className="set-ico mdi mdi-code-braces" style={{ color:'#e53935' }}></span><span className="label">AI Characters</span><span className="count">101</span></div>
            </div>
          </aside>
          <main className="main" style={{ position:'relative' }}>
            <div className="toolbar">
              <div className="tb-left">
                <div className="split">
                  <button className="bbtn bbtn--icon"><span className="mdi mdi-sort-ascending"></span></button>
                  <button className="bbtn"><span className="prefix">Sort:</span><span className="mdi mdi-calendar"></span><span>Date Created</span><span className="mdi mdi-menu-down chev"></span></button>
                </div>
                <button className="bbtn"><span className="mdi mdi-filter-outline"></span><span className="mdi mdi-menu-down chev"></span></button>
                <button className="bbtn"><span className="mdi mdi-view-grid"></span><span className="mdi mdi-menu-down chev"></span></button>
              </div>
              <div className="tb-right">
                <button className="bbtn bbtn--icon" title="Settings"><span className="mdi mdi-cog-outline"></span></button>
              </div>
            </div>
            <div className="grid-scroll">
              <div className="cgrid">
                {GRID.slice(0, 12).map((t, i) => <div key={i} className="cell"><img src={`../../assets/samples/${t}.webp`} alt="" /></div>)}
              </div>
            </div>
            {open && (
              <div className="ctx" style={{ left:-64, top:132 }}>
                <div className="ctx-label">Release Set B</div>
                <div className="ctx-item"><span className="mdi mdi-pencil-outline"></span>Edit set…</div>
                <div className="ctx-item"><span className="mdi mdi-tray-arrow-down"></span>Export to zip</div>
                <div className="ctx-div"></div>
                <div className="ctx-item hi"><span className="mdi mdi-content-duplicate"></span><span className="sp">Find duplicates in this set</span><span className="cnt">18</span></div>
                <div className="ctx-item"><span className="mdi mdi-image-multiple"></span><span className="sp">Filter to stacks in this set</span><span className="cnt">7</span></div>
                <div className="ctx-div"></div>
                <div className="ctx-item"><span className="mdi mdi-lock-outline"></span>Lock set</div>
                <div className="ctx-item danger"><span className="mdi mdi-trash-can-outline"></span>Delete set…</div>
              </div>
            )}
          </main>
        </div>
      </div>
    </div>
  );
}

function Spec({ title, note, children, wide }) {
  return (
    <div className="spec" style={wide ? { gridColumn:'span 2' } : null}>
      <div className="stage">{children}</div>
      <div className="cap"><b>{title}</b><span>{note}</span></div>
    </div>
  );
}

function Page() {
  return (
    <div className="page">
      <div className="lede">
        <div>
          <h1>Dedup <span className="s">→</span> Stacks</h1>
          <p>1.9.0. Duplicate detection becomes a destination with a count, and its only verdict is <b>stack</b> or <b>keep separate</b>. Nothing is deleted — that's a later release.</p>
        </div>
        <div className="note-card warn">
          <h3>Why the sort order has to go</h3>
          <p>“Similarity to …” is a <b>lens</b>, not a task. It never tells you how many duplicates you have, offers no verdict, has no bulk action, and forgets everything the moment you change sort. Users don't discover it, and when they do they can't finish anything with it.</p>
          <p>It also implies a full-library comparison every time you use it — the thing that makes Immich's dedup unusable at scale.</p>
        </div>
        <div className="note-card good">
          <h3>What replaces it</h3>
          <p><b>Tiered detection</b> — exact matches come from an indexed hash query in milliseconds; near-dupes are found inside candidate buckets, streamed in, and cached forever.</p>
          <p><b>A triage queue</b> — one group on screen, cover preselected with reasons shown, one keystroke, auto-advance. Designed for groups-per-minute, not groups-displayed.</p>
        </div>
      </div>

      <div className="sect">
        <div className="sect-head"><h2>1 · Detection strategy</h2><span>The part that keeps it fast on 128k pictures — and the reason it isn't Immich's model</span></div>
        <div className="tiers">
          <div className="tier">
            <div className="th"><span className="mdi mdi-approximately-equal"></span><b>Tier 1 · Exact</b><span className="cost">~200 ms</span></div>
            <p>A hash column with an index. <code>GROUP BY hash HAVING count(*) &gt; 1</code>. No ML, no image decode, no GPU.</p>
            <ul>
              <li>Typically 60–80% of a real backlog</li>
              <li>Zero human judgment needed → bulk auto-stack</li>
              <li>Runs on import, incrementally</li>
            </ul>
          </div>
          <div className="tier">
            <div className="th"><span className="mdi mdi-view-grid-outline"></span><b>Tier 2 · Bucketed near</b><span className="cost">seconds/bucket</span></div>
            <p>Perceptual hash compared <b>only within candidate buckets</b> — same dimensions, same capture minute, same import batch, same folder. Never library-wide.</p>
            <ul>
              <li>Turns O(n²) into many tiny problems</li>
              <li>Catches bursts, re-exports, resizes</li>
              <li>Streams groups as buckets finish</li>
            </ul>
          </div>
          <div className="tier">
            <div className="th"><span className="mdi mdi-brain"></span><b>Tier 3 · Embedding</b><span className="cost">background, opt-in</span></div>
            <p>Full ML similarity for cross-folder, differently-framed near-dupes. Expensive, so it's a background job the user opts into — never a prerequisite for seeing results.</p>
            <ul>
              <li>Reuses embeddings the app already computes</li>
              <li>Results append to the same queue</li>
              <li>Cached; only new assets rescan</li>
            </ul>
          </div>
        </div>
        <div className="note-card">
          <h3>Three rules that keep it usable at any size</h3>
          <p><b>Never block on a full pass.</b> The queue opens with whatever's been found so far and a “scanned 62% of 128,412” banner. Immich's review page waits for everything, then tries to render it all.</p>
          <p><b>Virtualize the queue, don't paginate a page.</b> One group is in the DOM at a time. 10 groups and 10,000 groups perform identically.</p>
          <p><b>Persist verdicts.</b> “Keep separate” is remembered per group signature, so a rescan never re-asks. This is what makes the count trustworthy — and what the sort order could never do.</p>
        </div>
      </div>

      <div className="sect">
        <div className="sect-head"><h2>2 · The review queue, live</h2><span>↑↓ chooses the group the keyboard acts on — the accent bar and caret mark it. Enter stacks it, S keeps it separate, C compares.</span></div>
        <Queue />
      </div>

      <div className="sect">
        <div className="sect-head"><h2>3 · Stacks in the grid</h2><span>The result — All Pictures keeps its normal toolbar; the grid-view button expands stacks</span></div>
        <GridStacks />
        <div className="board">
          <Spec title="Collapsed stack" note="Matches the grid's existing badge vocabulary — the same translucent chip and mdi-image-multiple icon the selection bar already uses — plus the count. No new chrome: a stack is a picture first.">
            <div className="demo-cell">
              <img src="../../assets/samples/tile-08.webp" alt="" />
              <span className="stackbadge"><span className="mdi mdi-image-multiple"></span>4</span>
            </div>
          </Spec>
          <Spec title="Suggested, not yet stacked" note="Groups still waiting in the queue use the duplicate icon and a ? in place of a count — never a fake state change. Clicking it jumps to that group in the queue.">
            <div className="demo-cell">
              <img src="../../assets/samples/tile-03.webp" alt="" />
              <span className="stackbadge" style={{ color:'var(--text-muted)' }}><span className="mdi mdi-content-duplicate"></span>3?</span>
            </div>
          </Spec>
          <Spec title="Kept separate" note="No marker at all. The group is remembered as resolved so no rescan re-suggests it, but the grid shows nothing — the user's decision is that these are simply different pictures.">
            <div className="demo-cell"><img src="../../assets/samples/scene-03.webp" alt="" /></div>
          </Spec>
        </div>
      </div>

      <div className="sect">
        <div className="sect-head"><h2>4 · Duplicates in context</h2><span>Right-click any project, set, character or folder — dedup scoped to that data set</span></div>
        <ContextEntry />
        <div className="board">
          <Spec title="Scoped queue header" note="Choosing the context item opens the same queue with a scope pill in place of the whole-library scope. Dismissing the pill widens back to the full library without losing your position.">
            <div style={{ display:'flex', alignItems:'center', gap:'var(--space-3)', flexWrap:'wrap', justifyContent:'center' }}>
              <span className="qtitle" style={{ fontSize:'var(--text-md)', fontWeight:'var(--weight-semibold)' }}>Group 1</span>
              <span className="qsub" style={{ fontSize:'var(--text-sm)', color:'var(--text-muted)' }}>of 18</span>
              <span className="scopepill"><span className="mdi mdi-folder-multiple-image"></span>Release Set B<button className="mdi mdi-close"></button></span>
            </div>
          </Spec>
          <Spec title="Why it belongs on the row" note="A scoped scan is a fraction of the work of a library scan — usually instant. Putting it on the object the user is already looking at is the difference between “dedup this shoot” being a two-second job and a global chore.">
            <div style={{ display:'flex', flexDirection:'column', gap:'var(--space-2)', width:'100%', maxWidth:300 }}>
              <div className="ctx-item hi" style={{ borderRadius:'var(--radius-sm)' }}><span className="mdi mdi-folder-outline"></span><span className="sp">Find duplicates in this folder</span><span className="cnt">4</span></div>
              <div className="ctx-item hi" style={{ borderRadius:'var(--radius-sm)' }}><span className="mdi mdi-account-box-outline"></span><span className="sp">Find duplicates for Walter</span><span className="cnt">2</span></div>
              <div className="ctx-item hi" style={{ borderRadius:'var(--radius-sm)' }}><span className="mdi mdi-briefcase-outline"></span><span className="sp">Find duplicates in this project</span><span className="cnt">61</span></div>
            </div>
          </Spec>
          <Spec title="Zero-state" note="When a scoped scan finds nothing, the menu item still appears but reads as resolved rather than vanishing — an absent item reads as a missing feature, a zero reads as an answer.">
            <div className="ctx-item" style={{ borderRadius:'var(--radius-sm)', opacity:.55 }}><span className="mdi mdi-check"></span><span className="sp">No duplicates in this set</span></div>
          </Spec>
        </div>
      </div>

      <div className="sect">
        <div className="sect-head"><h2>5 · Bulk: the fast lane</h2><span>Exact matches never deserve a human</span></div>
        <AutoStackDialog />
      </div>

      <div className="sect">
        <div className="sect-head"><h2>6 · Migrating off Similarity Groups</h2><span>What happens to the existing sort order</span></div>
        <div className="migrate">
          <div className="mcol old">
            <h4>1.8 — Sort order</h4>
            <div style={{ display:'flex', gap:'var(--space-2)', marginBottom:'var(--space-3)' }}>
              <span className="togglebtn gone"><span className="mdi mdi-account-search-outline"></span>Similarity to …</span>
            </div>
            <p>Reorders the grid so lookalikes sit next to each other. No count, no verdict, no memory. Discoverable only by opening the sort menu and reading every option.</p>
            <p>Removed from the sort menu in 1.9.0.</p>
          </div>
          <div className="marrow"><span className="mdi mdi-arrow-right"></span></div>
          <div className="mcol">
            <h4>1.9 — Destination + queue</h4>
            <div style={{ display:'flex', gap:'var(--space-2)', marginBottom:'var(--space-3)', flexWrap:'wrap' }}>
              <span className="togglebtn on"><span className="mdi mdi-content-duplicate"></span>Duplicates <span style={{ background:'rgba(0,0,0,.25)', borderRadius:99, padding:'0 6px', fontSize:11 }}>143</span></span>
              <span className="togglebtn"><span className="mdi mdi-filter-outline"></span>Filter › Stacked only</span>
            </div>
            <p>A sidebar entry with a live count that goes down as you work, and a queue that produces stacks. Stacked / unstacked becomes a <b>filter</b> rather than a second destination — only the thing with a to-do count earns a sidebar row. The similarity <i>signal</i> survives: it now builds groups instead of shuffling the grid.</p>
            <p>On first launch after upgrade: a one-line notice in the sort menu's place pointing at the new sidebar entry, shown once.</p>
          </div>
        </div>
      </div>

      <div className="sect">
        <div className="sect-head"><h2>7 · Rules</h2><span>Handoff</span></div>
        <div className="rulelist">
          <div className="rule"><span className="rn mdi mdi-star-outline"></span><p><b>Cover selection</b>Score = <code>pixels×4 + tags×3 + userScore×2 + RAW bonus</code>. Highest wins, ties break to oldest capture time. Always shown as a preselection the user can override with 1–9; never silent.</p></div>
          <div className="rule"><span className="rn mdi mdi-shield-check-outline"></span><p><b>No deletion in 1.9</b>Every member file stays on disk and in the database. A stack is a grouping row plus a cover pointer — dropping it restores the flat grid exactly. This is what makes the whole feature safe to ship without confirmation dialogs.</p></div>
          <div className="rule"><span className="rn mdi mdi-tag-multiple-outline"></span><p><b>Metadata union</b>Stacking unions tags, characters and Set membership onto the stack, and takes the highest score. Nothing is overwritten or lost — the reason Immich users get burned is albums silently breaking, and a union can't break anything.</p></div>
          <div className="rule"><span className="rn mdi mdi-view-sequential-outline"></span><p><b>Rows, not one-at-a-time</b>The queue shows as many groups as fit. Exactly one row is focused — accent left bar, caret, tinted background, filled Stack button and a “keyboard acts here” label — so <code>↑↓</code> then <code>Enter</code>/<code>S</code> can never be ambiguous about which group it hits.</p></div>
          <div className="rule"><span className="rn mdi mdi-image-size-select-actual"></span><p><b>Thumbnails carry no metadata</b>Queue rows show pictures at grid scale, edge to edge, with only the cover label and the index while focused. Resolution, size, date and tag counts live in Compare, in two columns with de-emphasised labels so the values read first and the metadata never squeezes the image.</p></div>
          <div className="rule"><span className="rn mdi mdi-close-circle-outline"></span><p><b>Signals cut both ways</b>Matching evidence is an olive check pill; anything arguing against a stack — different resolution, different aspect ratio, subject moved, eyes closed — is a red × pill. A group carrying red pills is exactly the one that needs Compare, so the pills do the warning rather than generic "review carefully" copy.</p></div>
          <div className="rule"><span className="rn mdi mdi-folder-eye-outline"></span><p><b>Paths only where they matter</b>File location shows only for pictures in reference folders, where the user manages the files themselves and needs to know which copy is which. Managed library pictures hide it — there, the path is an implementation detail.</p></div>
          <div className="rule"><span className="rn mdi mdi-compare-horizontal"></span><p><b>Compare is a button, not a secret</b>Every row carries a <b>Compare all N</b> button showing <code>C</code> on the focused row. It opens the full field-by-field view — every candidate, best value highlighted per column, full paths — with Stack and Keep separate in its footer so the decision is made without a second trip.</p></div>
          <div className="rule"><span className="rn mdi mdi-filter-outline"></span><p><b>Stacks is a filter, not a place</b>A second row of the same large icon segments, directly under the media-type row — Any / Stacked / Unstacked / Unresolved duplicates — with the current choice spelled out beside it and the header's match count updating live. Only <b>Duplicates</b>, which has a to-do count, earns a sidebar entry.</p></div>
          <div className="rule"><span className="rn mdi mdi-shield-half-full"></span><p><b>Confidence tiers gate the queue</b>Exact matches are always included and can't be switched off. Each looser tier is a separate opt-in with its own count, and enabling one requires the tier above it — so a user can't land on “same scene” suggestions without having deliberately walked down to them. Turning on the loosest tier states the risk in place.</p></div>
          <div className="rule"><span className="rn mdi mdi-memory"></span><p><b>Queue is virtual</b>One group in the DOM. Prefetch the next group's thumbnails only. Group list is paged from the database by confidence descending; never loaded whole.</p></div>
          <div className="rule"><span className="rn mdi mdi-keyboard-outline"></span><p><b>Keyboard</b><code>Enter</code> stack · <code>S</code> keep separate · <code>1–9</code> set cover · <code>X</code> exclude the focused candidate · <code>Ctrl+Z</code> undo. Auto-advance after every verdict; the queue is designed to be worked without a mouse.</p></div>
          <div className="rule"><span className="rn mdi mdi-undo-variant"></span><p><b>Undo integration</b>Every verdict raises the standard action receipt and lands in the history stack. Bulk auto-stack coalesces into a single step, so 1,204 stacks reverse with one <code>Ctrl+Z</code>.</p></div>
          <div className="rule"><span className="rn mdi mdi-bookmark-check-outline"></span><p><b>Resolved memory</b>Verdicts are keyed on a group signature (sorted member hashes), so re-imports and rescans never re-ask. “Keep separate” is permanent until the user reopens it from the Stacks view.</p></div>
          <div className="rule"><span className="rn mdi mdi-crosshairs-gps"></span><p><b>Scoped entry points</b>Every collection object — project, set, character, folder — carries <b>Find duplicates in…</b> in its context menu with a live count. A scoped scan reuses cached hashes, so it returns instantly; the queue opens with a dismissible scope pill.</p></div>
          <div className="rule"><span className="rn mdi mdi-tune-variant"></span><p><b>Threshold</b>Default 0.90. Exact matches are always shown. Below 0.65 nothing is suggested at all — a low threshold produces confident-looking garbage and destroys trust in the count.</p></div>
        </div>
      </div>
    </div>
  );
}

if (window.__DEDUP_HOST) ReactDOM.createRoot(document.getElementById('root')).render(<Page />);
