"use strict";
// Context comes from the current plan and the server's permission-filtered memories.
window.MeriContext = (() => {
  const sampleNames = {maya:"Maya", theo:"Theo"}; // same sample people as the gallery
  let offered = null;
  function candidates() {
    const memories = state.memories.filter(m => m.building_id === state.plan.building.id);
    const rank = (a,b) => (relative(a).dist <= 4 ? 0 : 1) - (relative(b).dist <= 4 ? 0 : 1)
      || (b.created_at || "").localeCompare(a.created_at || "");
    return {
      own: memories.filter(m=>m.author_id===state.user).sort(rank)[0] || null,
      // 'family' here means someone has granted this viewer access through their circle.
      shared: memories.filter(m=>m.author_id!==state.user && m.visibility==='family').sort(rank)[0] || null
    };
  }
  function greeting() {
    const {own,shared}=candidates();
    const room = isInterior() ? sweepAt(state.walker.sweep)?.room : currentZone()?.name;
    const location = room ? `You're ${room==='Outside'?'outside':`in the ${room.toLowerCase()}`}.` : `You're at ${state.plan.building.name}.`;
    const parts=[`Hi, I'm Meri. ${location}`];
    offered={viewer:state.user,building:state.plan.building.id,own:own?.id,shared:shared?.id};
    if(own)parts.push(`You have a memory ${relative(own).dist<=4?'right here':'elsewhere here'}. Say "my memory" to revisit it.`);
    if(shared){
      const name=shared.author_name || sampleNames[shared.author_id];
      parts.push(`${name || 'Someone in your circle'} shared a recording with you${relative(shared).dist<=4?' nearby':''}. Say "shared memory" to listen.`);
    }
    if(!own && !shared)parts.push('There are no personal or circle memories here yet. Would you like to leave a memory or hear about this place?');
    else parts.push('Or ask where am I, tell me more, or leave a memory.');
    const text=parts.join(' ');
    $('whereText').textContent=text;
    return text;
  }
  function choose(kind) {
    // Never reuse a suggestion across users/buildings, or after access has changed.
    if(!offered || offered.viewer!==state.user || offered.building!==state.plan.building.id)return candidates()[kind];
    return state.memories.find(m=>m.id===offered[kind] && (kind==='own'?m.author_id===state.user:m.author_id!==state.user && m.visibility==='family')) || candidates()[kind];
  }
  return {greeting,choose,candidates};
})();
