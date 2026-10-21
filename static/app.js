'use strict';
const $ = (selector) => document.querySelector(selector);
const icons = {
  up: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M7 10v11H3V10h4Zm0 0 6-8c2 0 2 2 1 6h5a2 2 0 0 1 2 2l-2 9a2 2 0 0 1-2 2H7"/></svg>',
  down: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M7 14V3H3v11h4Zm0 0 6 8c2 0 2-2 1-6h5a2 2 0 0 0 2-2l-2-9a2 2 0 0 0-2-2H7"/></svg>',
  comment: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 11.5a8.4 8.4 0 0 1-.9 3.8A8.5 8.5 0 0 1 12.5 20a8.4 8.4 0 0 1-3.8-.9L3 21l1.9-5.7a8.4 8.4 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.4 8.4 0 0 1 3.8-.9h.5a8.5 8.5 0 0 1 8 8v.5Z"/></svg>'
};
const state = { feed: null, filter: 'All stories', demo: false, activeArticle: null, demoComments: {}, loading: false };
let toastTimer, commentRequest = 0;
function escapeHTML(value) {
  return String(value).replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
function timeAgo(timestamp) {
  const minutes = Math.max(0, Math.floor((Date.now()/1000 - timestamp)/60));
  if (minutes < 1) return 'just now';
  if (minutes < 60) return `${minutes}m ago`;
  if (minutes < 1440) return `${Math.floor(minutes/60)}h ago`;
  return `${Math.floor(minutes/1440)}d ago`;
}
function toast(message) {
  $('#toast').textContent = message;
  $('#toast').hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { $('#toast').hidden = true; }, 4000);
}
async function api(path, options = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 20000);
  try {
    const response = await fetch(path, { ...options, signal: controller.signal, headers: { 'Content-Type': 'application/json', ...options.headers } });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Something went wrong. Please try again.');
    return data;
  } catch (error) {
    if (error.name === 'AbortError') throw new Error('The request timed out. Please try again.');
    throw error;
  } finally { clearTimeout(timer); }
}
function articles() { return [...(state.feed?.articles || []), ...(state.feed?.deep_read ? [state.feed.deep_read] : [])]; }
function actions(article) {
  return `<div class="story-actions"><button class="vote-button" data-vote="1" data-id="${article.id}" aria-pressed="${article.my_vote===1}" aria-label="Thumbs up: ${escapeHTML(article.title)}">${icons.up}<span>${article.up}</span></button><button class="vote-button" data-vote="-1" data-id="${article.id}" aria-pressed="${article.my_vote===-1}" aria-label="Thumbs down: ${escapeHTML(article.title)}">${icons.down}<span>${article.down}</span></button><button class="comment-button" data-comments="${article.id}" aria-label="Discuss: ${escapeHTML(article.title)}">${icons.comment}<span>${article.comments} <span class="comments-word">comments</span></span></button>${article.kind==='news' ? `<a class="story-link" href="${escapeHTML(article.url)}" target="_blank" rel="noopener noreferrer">${state.demo?'Visit source':'Read story'} <span class="arrow-icon" aria-hidden="true"></span></a>` : ''}</div>`;
}
function renderNews() {
  const all = state.feed?.articles || [];
  const filtered = all.filter(a => state.filter === 'All stories' || a.category === state.filter);
  $('#story-count').textContent = String(all.length).padStart(2, '0');
  if (!all.length) {
    $('#news-list').innerHTML = `<div class="empty-state"><span class="empty-icon">⌁</span><h3>${state.feed?.refreshing?'Finding the signal.':'A little quiet here, for now.'}</h3><p>${state.feed?.refreshing?'We’re checking the publisher feeds. The shortlist will update as soon as stories arrive.':'No recent stories are available. Publisher feeds may be unreachable. Check source status or explore an explicitly labeled sample briefing.'}</p><button id="demo-button">Explore a sample briefing <span class="arrow-icon" aria-hidden="true"></span></button></div>`;
    return;
  }
  if (!filtered.length) {
    $('#news-list').innerHTML = '<div class="empty-state"><span class="empty-icon">⌁</span><h3>No stories in this category.</h3><p>Try another category or come back after the next update.</p><button id="reset-filter">See all stories <span class="arrow-icon" aria-hidden="true"></span></button></div>';
    return;
  }
  $('#news-list').innerHTML = filtered.map(a => `<article class="news-card"><span class="rank">${String(all.indexOf(a)+1).padStart(2,'0')}</span><div><div class="story-meta"><span class="category-tag">${escapeHTML(a.category)}</span><span class="source">${escapeHTML(a.source)}</span><span>·</span><time datetime="${new Date(a.published*1000).toISOString()}">${state.demo?'Sample':timeAgo(a.published)}</time></div><h3><a href="${escapeHTML(a.url)}" target="_blank" rel="noopener noreferrer">${escapeHTML(a.title)} <span class="external-arrow"><span class="arrow-icon" aria-hidden="true"></span></span></a></h3><p>${escapeHTML(a.description || 'Read the full reporting at the original publisher.')}</p>${actions(a)}</div></article>`).join('');
}
function renderDeep() {
  const a = state.feed?.deep_read;
  if (!a) {
    $('#deep-content').innerHTML = '<div class="deep-empty"><span class="eyebrow">A MOMENT FOR PERSPECTIVE</span><h2>Make room for<br>a bigger idea.</h2><p>No recent analysis is available yet. Our recommended read will appear here when the analysis feeds connect.</p></div>';
    return;
  }
  $('#deep-content').innerHTML = `<div class="deep-kicker">${escapeHTML(a.source)} <span>·</span> ${state.demo?'SAMPLE ARTICLE':timeAgo(a.published)}</div><h2><a href="${escapeHTML(a.url)}" target="_blank" rel="noopener noreferrer">${escapeHTML(a.title)}</a></h2><p>${escapeHTML(a.description || 'Explore the full argument at the original publisher.')}</p><div class="why-read"><strong>Why make time for it</strong><p>A longer perspective from our AI analysis sources, selected to look beyond the latest headlines.</p></div><a class="read-deep" href="${escapeHTML(a.url)}" target="_blank" rel="noopener noreferrer">${state.demo?'Visit sample source':'Read the full perspective'} <span><span class="arrow-icon" aria-hidden="true"></span></span></a>${actions(a)}`;
}
function renderSources() {
  const sources = ['TechCrunch','The Guardian','MIT Technology Review','AI Snake Oil'];
  $('#source-list').innerHTML = sources.map(name => {
    const source = state.feed?.sources?.find(s => s.name===name);
    const ok = source && !source.error && source.success;
    return `<div class="source-row"><strong>${name}</strong><span class="${ok?'connected':''}">${ok?'Connected · '+source.count+' articles':source?.error?'Unavailable': 'Awaiting first check'}</span></div>`;
  }).join('');
}
function render() {
  renderNews(); renderDeep(); renderSources();
  const feed = state.feed;
  const failed = feed?.sources?.filter(s => s.error).length || 0;
  $('#notice').hidden = true;
  if (state.demo) {
    $('#notice').hidden = false;
    $('#notice').innerHTML = '<strong>Sample briefing.</strong> These are illustrative headlines, not live news. Links visit source homepages. Sample feedback lasts only until you leave this view. <button id="exit-demo">Return to live feed <span class="arrow-icon" aria-hidden="true"></span></button>';
    $('#update-status').textContent = 'SAMPLE EDITION · NOT LIVE NEWS';
  } else {
    $('#update-status').textContent = feed?.refreshing ? 'Checking publisher feeds…' : feed?.last_updated ? `Last fetched ${timeAgo(feed.last_updated)}` : 'Waiting for publisher feeds';
    if (failed || feed?.stale && feed?.last_updated) {
      $('#notice').hidden = false;
      $('#notice').textContent = failed ? `${failed} publisher ${failed===1?'feed is':'feeds are'} unavailable. Showing recent cached stories where available. Open “Our sources” for details.` : 'The feed has not refreshed recently. Showing cached reporting with original publication times.';
    }
  }
}
async function loadFeed({silent=false}={}) {
  if (state.loading) return;
  state.loading = true;
  $('#refresh-button').disabled = true;
  try {
    const feed = await api('/api/feed');
    if (!state.demo) { state.feed = feed; render(); }
    if (!silent && feed.last_updated) toast('Briefing updated. Sources are checked every 15 minutes.');
  } catch (error) {
    if (!silent || !state.feed) {
      $('#notice').textContent = 'Could not reach the server. Please check your connection and refresh.';
      $('#notice').hidden = false;
      if (!state.feed) { state.feed={articles:[],sources:[]}; renderNews(); }
      toast(error.message);
    }
  } finally { state.loading=false; $('#refresh-button').disabled=false; }
}
function setFilter(filter) {
  state.filter=filter;
  document.querySelectorAll('[data-filter]').forEach(button=>{
    const selected=button.dataset.filter===filter;
    button.classList.toggle('selected',selected); button.setAttribute('aria-pressed',String(selected));
  });
  renderNews();
}
function startDemo() {
  state.demo=true;
  const examples = [
    ['Models & tools','TechCrunch','Smaller models, bigger possibilities: the move toward on-device AI','An illustrative look at why efficient models could bring useful AI tools to more devices, with fewer cloud dependencies.'],
    ['Research','The Guardian','What should an AI benchmark actually measure?','An example story exploring the gap between impressive test scores and reliable performance in everyday work.'],
    ['Industry','TechCrunch','The next chapter for AI startups is about real-world usefulness','A sample report on how teams might move from eye-catching demos to products people return to every day.'],
    ['Policy & society','The Guardian','Who gets a say in the future of artificial intelligence?','This illustrative headline asks how communities, researchers, and policymakers can shape decisions about new technology.'],
    ['Models & tools','TechCrunch','AI coding assistants are changing how teams review software','A sample story about the opportunities and tradeoffs of bringing AI tools into the development process.'],
    ['Research','The Guardian','Beyond the chatbot: a closer look at AI for scientific discovery','An illustrative overview of how machine learning can support researchers while leaving careful validation to people.'],
    ['Policy & society','The Guardian','The open questions around AI, authorship, and creative work','A sample conversation about consent, attribution, and the changing relationship between tools and the people who use them.'],
    ['Industry','TechCrunch','Building the infrastructure behind the next generation of AI','An example story examining the energy, hardware, and engineering choices that sit beneath the interface.'],
    ['Models & tools','TechCrunch','A more practical approach to personal AI assistants','An illustrative look at what it would take to make assistants useful, trustworthy, and understandable.'],
    ['Research','The Guardian','Why understanding a model’s limits matters as much as its capabilities','A sample report about uncertainty, evaluation, and knowing when a system needs human judgment.']
  ];
  const now=Date.now()/1000;
  const demoArticles=examples.map(([category,source,title,description],i)=>({id:`sample-${i}`,category,source,title,description,kind:'news',published:now-i*3600,url:source==='TechCrunch'?'https://techcrunch.com/category/artificial-intelligence/':'https://www.theguardian.com/technology/artificialintelligenceai',up:0,down:0,my_vote:0,comments:0}));
  state.feed={articles:demoArticles,deep_read:{id:'sample-deep',title:'What do we lose when we outsource our thinking?',description:'An illustrative deep read: as AI makes it easier to find an answer, the more interesting question may be how we preserve the habit of asking better questions.',source:'AI Snake Oil',kind:'deep',category:'Policy & society',published:now,url:'https://www.aisnakeoil.com/',up:0,down:0,my_vote:0,comments:0},sources:[]};
  state.demoComments={}; setFilter('All stories'); render();
}
async function vote(button) {
  const article=articles().find(a=>a.id===button.dataset.id);
  if (!article) return;
  const direction=Number(button.dataset.vote);
  const value=article.my_vote===direction?0:direction;
  const buttons=document.querySelectorAll(`[data-vote][data-id="${article.id}"]`);
  buttons.forEach(b=>b.disabled=true);
  try {
    if (state.demo) {
      if(article.my_vote===1) article.up--; if(article.my_vote===-1) article.down--;
      article.my_vote=value;
      if(value===1) article.up++; if(value===-1) article.down++;
    } else {
      const updated=await api(`/api/articles/${article.id}/vote`,{method:'POST',body:JSON.stringify({value})});
      Object.assign(article,updated);
    }
    renderNews();renderDeep();
  } catch(error) { toast(error.message); }
  finally { buttons.forEach(b=>b.disabled=false); }
}
function renderComments(comments) {
  $('#comments-list').innerHTML=comments.length?comments.map(c=>`<article class="comment"><div class="comment-header"><span class="comment-avatar">${escapeHTML(c.name.slice(0,1).toUpperCase())}</span><strong>${escapeHTML(c.name)}</strong><time datetime="${new Date(c.created*1000).toISOString()}">${timeAgo(c.created)}</time></div><p>${escapeHTML(c.body)}</p></article>`).join(''):'<p>Be the first to add a perspective.</p>';
}
async function fetchComments(article) {
  const request=++commentRequest;
  try {
    const data=state.demo?{comments:state.demoComments[article.id]||[]}:await api(`/api/articles/${article.id}/comments`);
    if (request===commentRequest && state.activeArticle?.id===article.id) renderComments(data.comments);
  } catch(error) { if(request===commentRequest) $('#comments-list').textContent=error.message; }
}
function openComments(id) {
  const article=articles().find(a=>a.id===id);
  if(!article) return;
  state.activeArticle=article;
  $('#discussion-title').textContent=article.title;
  $('#comment-error').textContent=''; $('#comment-body').value='';
  try { $('#comment-name').value=localStorage.getItem('signal-name')||''; } catch {}
  $('#comments-list').textContent='Loading the conversation…';
  $('#discussion-dialog').showModal();
  fetchComments(article);
}
$('#comment-form').addEventListener('submit',async event=>{
  event.preventDefault();
  const article=state.activeArticle;
  const name=$('#comment-name').value.trim(),body=$('#comment-body').value.trim();
  if(!name||!body) { $('#comment-error').textContent='Please add your name and a comment.';return; }
  const button=$('#comment-form button[type=submit]');button.disabled=true;$('#comment-error').textContent='';
  try {
    if(state.demo) {
      (state.demoComments[article.id]??=[]).unshift({name,body,created:Date.now()/1000});
    } else await api(`/api/articles/${article.id}/comments`,{method:'POST',body:JSON.stringify({name,body})});
    try {localStorage.setItem('signal-name',name);} catch {}
    article.comments++; $('#comment-body').value='';
    renderNews(); renderDeep(); await fetchComments(article); toast(state.demo?'Sample comment added for this session.':'Your perspective is in. Thank you.');
  } catch(error) { $('#comment-error').textContent=error.message; }
  finally {button.disabled=false;}
});
document.addEventListener('click',event=>{
  const button=event.target.closest('button'); if(!button)return;
  if(button.dataset.filter)setFilter(button.dataset.filter);
  if(button.dataset.vote)vote(button);
  if(button.dataset.comments)openComments(button.dataset.comments);
  if(button.dataset.close)$('#'+button.dataset.close).close();
  if(button.id==='demo-button')startDemo();
  if(button.id==='reset-filter')setFilter('All stories');
  if(button.id==='exit-demo') {state.demo=false;state.feed={articles:[],deep_read:null,sources:[]};setFilter('All stories');render();loadFeed();}
});
['sources-button','footer-sources','how-button'].forEach(id=>$('#'+id).addEventListener('click',()=>{renderSources();$('#sources-dialog').showModal();}));
$('#refresh-button').addEventListener('click',()=>{if(state.demo)toast('This is a sample edition. Return to the live feed to check for news.');else loadFeed();});
document.querySelectorAll('dialog').forEach(dialog=>dialog.addEventListener('click',event=>{if(event.target===dialog){const rect=dialog.getBoundingClientRect();if(event.clientX<rect.left||event.clientX>rect.right||event.clientY<rect.top||event.clientY>rect.bottom)dialog.close();}}));
$('#edition-date').textContent=new Date().toLocaleDateString(undefined,{weekday:'long',month:'long',day:'numeric'}).toUpperCase();
loadFeed({silent:true});
setInterval(()=>{if(!state.demo&&!document.hidden&&!$('#discussion-dialog').open)loadFeed({silent:true});},30000);
