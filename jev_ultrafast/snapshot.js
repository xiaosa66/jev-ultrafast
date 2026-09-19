(() => {
  if (!document.body) return null;
  const cache = window.__jevFast ||= {ids:new WeakMap(), nodes:new Map(), next:1};
  const identity = e => {
    if (!cache.ids.has(e)) cache.ids.set(e,cache.next++);
    const id=cache.ids.get(e); cache.nodes.set(id,e); return id;
  };
  for (const [id,e] of cache.nodes) if (!e.isConnected) cache.nodes.delete(id);
  const safe = e => !['password','file','hidden'].includes(e.type);
  const visible = e => !e.closest('[aria-hidden="true"],[inert]') &&
    e.checkVisibility({checkOpacity:true,checkVisibilityCSS:true});
  // UI kits (antd, MUI, bit-ui, ...) hide the native checkbox/radio -- opacity:0,
  // 0x0, or clipped -- and paint a span on top. The input is then invisible to
  // checkVisibility even though the control is plainly on screen. Fall back to the
  // visible clickable wrapper (its <label>), which toggles the same input.
  const proxy = e => {
    if (!e || e.tagName !== 'INPUT' || !['checkbox','radio'].includes(e.type)) return null;
    if (visible(e) && e.getBoundingClientRect().width > 0) return null;
    const w = e.closest('label') ||
      e.closest('[role="checkbox"],[role="radio"],[role="switch"]') || e.parentElement;
    if (!w || !visible(w)) return null;
    return w.getBoundingClientRect().width > 0 ? w : null;
  };
  const name = (e,seen=new Set()) => {
    if (!e || seen.has(e)) return '';
    seen.add(e);
    const referenced=(e.getAttribute('aria-labelledby')||'').split(/\s+/)
      .map(id=>name(document.getElementById(id),seen)).filter(Boolean).join(' ');
    return referenced || e.getAttribute('aria-label') ||
      [...(e.labels||[])].map(l=>name(l,seen)).filter(Boolean).join(' ') ||
      (['button','submit','reset'].includes(e.type) ? e.value : '') || e.getAttribute('alt') ||
      (e.tagName==='INPUT' ? '' : [...e.childNodes].map(n=>n.nodeType===3 ? n.textContent :
        n.nodeType===1 && n.getAttribute('aria-hidden')!=='true' ? name(n,seen) : '').join(' ').trim()) ||
      e.getAttribute('title') || e.getAttribute('placeholder') || '';
  };
  // Many trading/dashboard UIs ship inputs with no aria-label, placeholder or <label>
  // (react/bit-ui wraps them in styled spans). Their surrounding text is the only
  // name available; walk outwards and collect the preceding sibling texts.
  const contextLabel = e => {
    const parts = [];
    let node = e;
    for (let i = 0; i < 4 && node?.parentElement; i++) {
      let s = node.previousElementSibling;
      while (s) {
        const t = (s.innerText || '').replace(/\s+/g, ' ').trim();
        if (t && t.length < 40) { parts.unshift(t); break; }
        s = s.previousElementSibling;
      }
      node = node.parentElement;
    }
    return parts.filter((t,i,arr)=>arr.indexOf(t)===i).join(' ').slice(0, 60);
  };
  const roles=['button','link','checkbox','radio','switch','tab','menuitem','menuitemradio',
    'option','gridcell','combobox','textbox','searchbox','spinbutton'];
  const selector='a[href],button,input,textarea,select,summary,[contenteditable="true"],'+
    roles.map(role=>'[role="'+role+'"]').join(',');
  const role = e => {
    const explicit=e.getAttribute('role');
    if (roles.includes(explicit)) return explicit;
    if (e.tagName==='BUTTON' || e.tagName==='SUMMARY') return 'button';
    if (e.tagName==='A') return 'link';
    if (e.tagName==='SELECT') return 'combobox';
    if (e.tagName==='TEXTAREA' || e.isContentEditable) return 'textbox';
    if (e.tagName==='INPUT') {
      if (['checkbox','radio'].includes(e.type)) return e.type;
      if (['button','submit','reset','image'].includes(e.type)) return 'button';
      if (e.type==='search') return 'searchbox';
      if (e.type==='number') return 'spinbutton';
      if (['text','email','url','tel'].includes(e.type)) return 'textbox';
    }
    return null;
  };
  cache.pageKey=()=>[performance.timeOrigin,location.href,scrollX,scrollY,innerWidth,innerHeight,
    [...document.querySelectorAll('input,textarea,select')].filter(safe)
      .map(e=>[identity(e),e.value,e.checked,e.selectedIndex,e.disabled,e.readOnly])];
  cache.guard=e=>{
    if (!e?.isConnected) return null;
    const geo=visible(e)?e:proxy(e);
    if (!geo) return null;
    const scope=geo.closest('form,dialog,[role="dialog"],article,li,tr,[role="row"]') || geo.parentElement;
    return [identity(e),role(e),name(e),e.value??null,e.checked??null,e.selectedIndex??null,
      e.readOnly??null,e.matches(':disabled'),e.getAttribute('aria-disabled'),
      e.getAttribute('aria-expanded'),e.getAttribute('aria-checked'),e.getAttribute('aria-selected'),
      e.getAttribute('href'),scope?.innerText?.slice(0,6000)||''];
  };
  const words=[], chunks=[],
    walker=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);
  const range=document.createRange(); let node,length=0;
  while ((node=walker.nextNode()) && length<6000) {
    const value=node.textContent.trim(), parent=node.parentElement;
    if (!value || !parent || parent.closest('script,style,noscript,template') || !visible(parent)) continue;
    range.selectNodeContents(node); const r=range.getBoundingClientRect();
    if (r.width>0 && r.height>0 && r.bottom>0 && r.top<innerHeight && r.right>0 && r.left<innerWidth) {
      words.push(value); length+=value.length;
      chunks.push({t:value, top:r.top, bottom:r.bottom, left:r.left, right:r.right});
    }
  }
  const text=words.join('\n').slice(0,6000);
  // Closest visible text above a control, roughly aligned with it. Form-kit inputs
  // (bit-form-item and friends) keep their label in a sibling column, so the only
  // reliable name is the text the user reads just above the field.
  const nearLabel = r => {
    let best=null, gap=1e9;
    for (const c of chunks) {
      const t=c.t;
      if (t.length > 24 || c.bottom > r.y + 6) continue;
      if (/^[\d.,%+\-/:\s]+$/.test(t)) continue;  // quotes and figures are not field names
      if (c.right < r.x - 80 || c.left > r.x + r.width + 80) continue;
      const d=r.y-c.bottom;
      if (d < gap) { gap=d; best=c.t; }
    }
    return gap < 40 ? best : '';
  };
  const actions=[];
  for (const e of document.querySelectorAll(selector)) {
    if (!safe(e) || e.matches(':disabled') || e.closest('[aria-disabled="true"]')) continue;
    let geo=e;
    if (!visible(e) || !e.getBoundingClientRect().width) {
      const p=proxy(e);
      if (!p) continue;
      geo=p;  // hidden native control: act on the visible wrapper, keep the input's semantics
    }
    const r=geo.getBoundingClientRect(), x=r.x+r.width/2, y=r.y+r.height/2, rname=role(e);
    if (!rname || r.width<=0 || r.height<=0 || x<0 || y<0 || x>=innerWidth || y>=innerHeight) continue;
    if (rname==='gridcell' && e.querySelector('button,[role="button"]')) continue;
    const base={node:identity(e),role:rname,
      label:name(geo)||name(e)||contextLabel(e)||nearLabel(r)||rname,
      rect:{x:r.x,y:r.y,w:r.width,h:r.height}};
    for (const key of ['checked','selected','expanded']) {
      const value=e.getAttribute('aria-'+key);
      if (value!==null) base[key]=value;
    }
    if (['checkbox','radio'].includes(e.type)) base.checked=String(e.checked);
    if (e.tagName==='SELECT') {
      for (const o of e.options) if (!o.selected && !o.disabled && !o.closest('optgroup[disabled]'))
        actions.push({...base,kind:'select',value:o.value,
          current_value:[...e.selectedOptions].map(o=>o.label).join(', '),label:base.label+' → '+o.label});
    } else {
      const editable=!e.readOnly && e.getAttribute('aria-readonly')!=='true' &&
        (['textbox','searchbox','spinbutton'].includes(rname) ||
          (rname==='combobox' && ['INPUT','TEXTAREA'].includes(e.tagName)));
      const value='value' in e ? String(e.value) :
        e.isContentEditable || rname==='combobox' ? e.innerText.trim() : '';
      actions.push({...base,kind:editable?'fill':'click',value});
      if (editable) actions.push({...base,kind:'click',value,label:'Open '+base.label});
    }
  }
  const height=document.documentElement.scrollHeight;
  const page_key=cache.pageKey(), guards={};
  for (const a of actions) if (!(a.node in guards)) guards[a.node]=cache.guard(cache.nodes.get(a.node));
  // Compare meaning and identity. Geometry is always resolved and hit-tested just before input.
  const semantics=actions.map(({rect,...action})=>action);
  // document.title and the visible text are deliberately left out: on live pages
  // (quotes, order books, unread counters) they change on every tick, which makes
  // fill actions permanently stale. location.href already covers navigation and
  // semantics covers structural change, so the guard stays just as meaningful.
  const marker=[performance.timeOrigin,location.href,scrollX,scrollY,innerWidth,innerHeight,
    semantics,page_key[6]];
  const omitted_actions=Math.max(0,actions.length-250);
  actions.splice(250);
  actions.forEach((a,i)=>a.id='e'+(i+1));
  if (scrollY+innerHeight<height-2) actions.push({id:'scroll_down',kind:'scroll',label:'Scroll down',delta:560});
  if (scrollY>0) actions.push({id:'scroll_up',kind:'scroll',label:'Scroll up',delta:-560});
  actions.push({id:'wait',kind:'wait',label:'Wait for the page to update'});
  return {url:location.href,title:document.title,w:innerWidth,h:innerHeight,text,
    scroll:{y:scrollY,height},actions,marker,page_key,guards,omitted_actions};
})()
