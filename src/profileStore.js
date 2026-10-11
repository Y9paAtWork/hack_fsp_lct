const KEY='fsp.candidate.v1';
export const emptyProfile=()=>({version:1,name:'',email:'',fsp:'',salaryMin:null,terms:false,step:1,selected:[],active:'',stacks:{},jobs:{},ownProjects:{},quiz:null});
const strings=x=>Array.isArray(x)?x.filter(v=>typeof v==='string'):[];
const records=(x,fields)=>Object.fromEntries(Object.entries(x&&typeof x==='object'&&!Array.isArray(x)?x:{}).filter(([,v])=>Array.isArray(v)).map(([key,rows])=>[key,rows.filter(r=>r&&typeof r==='object').map(r=>Object.fromEntries(fields.map(f=>[f,typeof r[f]==='string'?r[f]:''])))]));
export function sanitizeProfile(value){
  const p=emptyProfile();
  if(!value||typeof value!=='object')return p;
  for(const key of ['name','email','fsp','active'])if(typeof value[key]==='string')p[key]=value[key];
  p.salaryMin=Number.isSafeInteger(value.salaryMin)&&value.salaryMin>=1000&&value.salaryMin<=10000000?value.salaryMin:null;
  p.terms=value.terms===true;
  p.step=Number.isInteger(value.step)&&value.step>=1&&value.step<=4?value.step:1;
  p.selected=strings(value.selected);
  p.active=p.selected.includes(p.active)?p.active:(p.selected[0]||'');
  if(p.step>1&&!p.selected.length)p.step=Math.min(p.step,2);
  const rawStacks=value.stacks&&typeof value.stacks==='object'?value.stacks:{};
  const sharedStack=[...new Set(p.selected.flatMap(spec=>strings(rawStacks[spec])))];
  p.stacks=Object.fromEntries(p.selected.map(spec=>[spec,[...sharedStack]]));
  p.jobs=records(value.jobs,['company','role','start','end','description','source']);
  p.ownProjects=records(value.ownProjects,['name','description','tasks']);
  return p;
}
export function loadProfile(storage){try{return sanitizeProfile(JSON.parse(storage.getItem(KEY)||'null'))}catch{return emptyProfile()}}
export function saveProfile(storage,value){const p=sanitizeProfile(value);storage.setItem(KEY,JSON.stringify(p));return p}
export function deleteProfile(storage){storage.removeItem(KEY)}
export function profileSkills(profile){return [...new Set(profile.selected.flatMap(s=>profile.stacks[s]||[]))]}
