'use strict';
const video=document.getElementById('player'),standby=document.getElementById('standby'),enable=document.getElementById('enable'),statusLine=document.getElementById('status'),connection=document.getElementById('connection');
let source='',latest=null,polling=false;
const portrait=new URLSearchParams(location.search).get('layout')==='portrait';
function synchronize(state){latest=state;const url=state.song?.assets?.[portrait?'portrait':'landscape'];if(!url||!url.startsWith('/media/')){video.pause();standby.hidden=false;statusLine.textContent='Add a song and start the station in your studio.';return;}if(source!==url){source=url;video.src=url;video.load();}standby.hidden=true;if(!state.playing){video.pause();return;}if(video.readyState>=1){const target=Math.min(Math.max(0,state.position+(Date.now()/1000-state.server_time)),Math.max(0,video.duration-.05));if(Math.abs(video.currentTime-target)>1)video.currentTime=target;video.play().then(()=>enable.hidden=true).catch(()=>enable.hidden=false);}}
video.addEventListener('loadedmetadata',()=>{if(latest)synchronize(latest);});
enable.addEventListener('click',()=>{video.play().then(()=>enable.hidden=true).catch(()=>{});});
async function poll(){if(polling)return;polling=true;try{const response=await fetch('/api/playback',{cache:'no-store'});if(!response.ok)throw Error();const state=await response.json();connection.hidden=true;synchronize(state);}catch{connection.hidden=false;}finally{polling=false;}}
poll();setInterval(poll,750);
