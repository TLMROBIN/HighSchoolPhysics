/* A fixed saved deck; no teacher notes are shipped in presentation mode. */
(() => {
  'use strict';
  const slides = [...document.querySelectorAll('.deck-slide')];
  const prev = document.querySelector('[data-deck-prev]'), next = document.querySelector('[data-deck-next]');
  const jump = document.querySelector('[data-deck-jump]'), counter = document.querySelector('[data-deck-counter]');
  const notes = document.querySelector('[data-deck-notes]'), message = document.querySelector('[data-deck-message]');
  let index = 0, touch = null;
  const interactive = target => target.closest('button,input,textarea,select,a,label,[contenteditable="true"]');
  function overflow() {
    const body = slides[index].querySelector('.deck-body'), hint = slides[index].querySelector('.deck-overflow');
    if (body && hint) hint.hidden = body.scrollHeight <= body.clientHeight + 2 && body.scrollWidth <= body.clientWidth + 2;
  }
  function show(value) {
    index = Math.min(slides.length-1, Math.max(0, value));
    slides.forEach((slide, i) => { slide.hidden = i !== index; });
    prev.disabled = index === 0; next.disabled = index === slides.length-1;
    jump.value = index; counter.textContent = `${index+1} / ${slides.length}`;
    if (notes) notes.innerHTML = slides[index].querySelector('.deck-note-content')?.innerHTML || '<p>本页暂无备注</p>';
    location.hash = 'slide-' + (index+1); requestAnimationFrame(overflow);
  }
  prev.addEventListener('click', () => show(index-1)); next.addEventListener('click', () => show(index+1));
  jump.addEventListener('change', () => show(Number(jump.value)));
  window.addEventListener('keydown', event => {
    if (event.target.closest('input,textarea,select,a,[contenteditable="true"]') || event.altKey || event.ctrlKey || event.metaKey) return;
    if (event.target.closest('button') && [' ', 'Enter'].includes(event.key)) return;
    // Up/down retain their normal scrolling role inside long content.
    if (['ArrowRight','PageDown',' '].includes(event.key)) { event.preventDefault(); show(index+1); }
    if (['ArrowLeft','PageUp'].includes(event.key)) { event.preventDefault(); show(index-1); }
    if (event.key==='Home') { event.preventDefault(); show(0); }
    if (event.key==='End') { event.preventDefault(); show(slides.length-1); }
  });
  const stage = document.querySelector('.deck-stage');
  stage.addEventListener('click', event => {
    if (interactive(event.target) || window.getSelection()?.toString()) return;
    if (event.target===stage || event.target.classList.contains('deck-slide')) show(index+1);
  });
  stage.addEventListener('touchstart', event => {
    const scrollable=event.target.closest('table,.katex-display');
    touch = event.touches.length===1 && !interactive(event.target) && !scrollable ?
      {x:event.touches[0].clientX,y:event.touches[0].clientY} : null;
  }, {passive:true});
  stage.addEventListener('touchend', event => {
    if (!touch || !event.changedTouches.length) return;
    const dx=event.changedTouches[0].clientX-touch.x, dy=event.changedTouches[0].clientY-touch.y;
    if (Math.abs(dx)>70 && Math.abs(dx)>Math.abs(dy)*2) show(index+(dx<0?1:-1));
    touch=null;
  }, {passive:true});
  document.querySelector('[data-deck-fullscreen]').addEventListener('click', async () => {
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else await document.querySelector('.deck-shell').requestFullscreen();
    } catch (_) { message.textContent='此浏览器暂不支持全屏，可使用浏览器的全屏功能'; }
  });
  document.addEventListener('fullscreenchange', () => {
    document.querySelector('[data-deck-fullscreen]').textContent=document.fullscreenElement?'退出全屏':'全屏'; overflow();
  });
  if (typeof renderMathInElement==='function') {
    renderMathInElement(stage,{delimiters:[{left:'$$',right:'$$',display:true},{left:'\\[',right:'\\]',display:true},
      {left:'\\(',right:'\\)',display:false},{left:'$',right:'$',display:false}],throwOnError:false,trust:false});
  } else message.textContent='公式资源未加载，请检查资料包是否完整或刷新页面';
  window.addEventListener('resize', overflow); document.querySelectorAll('img').forEach(img => img.addEventListener('load', overflow));
  const match = location.hash.match(/^#slide-(\d+)$/); show(match ? Number(match[1])-1 : 0);
})();
