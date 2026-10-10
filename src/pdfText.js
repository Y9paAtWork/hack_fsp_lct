export function pageText(items) {
  const lines=[];
  for(const item of items) {
    if(typeof item.str!=='string'||!item.str)continue;
    const x=item.transform?.[4]??0,y=item.transform?.[5]??0;
    let line=lines.find(l=>Math.abs(l.y-y)<Math.max(2,(item.height||10)*0.25));
    if(!line){line={y,items:[]};lines.push(line);}
    line.items.push({...item,x});
  }
  return lines.sort((a,b)=>b.y-a.y).map(line=>{
    const ordered=line.items.sort((a,b)=>a.x-b.x);
    let text='',previous=null;
    for(const item of ordered){
      const gap=previous?item.x-(previous.x+(previous.width||0)):0;
      const threshold=Math.max(1,(item.height||10)*0.15);
      text+=(previous&&gap>threshold?' ':'')+item.str;
      previous=item;
    }
    return text;
  }).join('\n');
}
export function cleanResumeText(text) {
  return text.replace(/\u00ad/g,'').replace(/[\u200b-\u200d\ufeff]/g,'')
    .replace(/\u00a0/g,' ').replace(/([\p{L}])-\s*\n\s*([\p{L}])/gu,'$1$2')
    .replace(/[ \t]+/g,' ').replace(/\n{3,}/g,'\n\n').trim();
}
