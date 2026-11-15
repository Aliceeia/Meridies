"use strict";
// Static public environment only. Playback always asks for cached audio, never synthesis.
window.SceneSamples = (() => {
  let library = null;
  const ready = fetch('/scene-samples.json').then(r=>{
    if(!r.ok)throw Error('No prepared scene library');return r.json();
  }).then(value=>{library=value;}).catch(()=>{});
  function selected() {
    const building=library?.buildings[state.plan?.building.id];
    if(!building)return null;
    let position, heading;
    if(isInterior()){
      position=String(state.walker.sweep);
      heading=String((Math.round(norm360(state.walker.heading)/15)*15)%360);
    }else{
      const zone=currentZone();position=String(state.plan.zones.indexOf(zone));heading='0';
    }
    return building.positions[position]?.[heading] || null;
  }
  async function play(kind){
    await ready;
    const entry=selected();
    if(!entry){_toast('No prepared sample is available for this position yet.',5000);return;}
    stopVoice();
    const text=entry[kind];
    $(kind==='where'?'whereText':'tellText').textContent=text;
    Speech.say(text,{kind:kind==='where'?'detail':'more',force:true,prepared:true});
  }
  return {play,selected,ready};
})();
