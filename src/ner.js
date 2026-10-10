import {technologyCatalog} from './technologyCatalog.js';
const extraAliases = {
  Python: ['python', 'питон'], JavaScript: ['javascript', 'js'], TypeScript: ['typescript', 'ts'],
  React: ['react', 'react.js', 'reactjs'], Django: ['django'], FastAPI: ['fastapi'],
  PostgreSQL: ['postgresql', 'postgres', 'постгрес'], SQL: ['sql'], Docker: ['docker', 'докер'],
  Kubernetes: ['kubernetes', 'k8s'], Git: ['git'], Java: ['java'], 'C++': ['c++'], 'C#': ['c#'],
  'Node.js': ['node.js', 'nodejs'], Redis: ['redis'], MongoDB: ['mongodb'],
  PyTorch: ['pytorch'], TensorFlow: ['tensorflow'], pandas: ['pandas'], NumPy: ['numpy'],
  HTML: ['html'], CSS: ['css'], Linux: ['linux'], Go: ['golang', 'go'],
};

const aliases = {
  ...extraAliases, 'Vue.js':['vue','vuejs','vue.js'], 'Next.js':['nextjs','next.js'],
  'Express.js':['express','expressjs','express.js'], Kubernetes:['kubernetes','k8s','кубернетес'],
  'scikit-learn':['scikit-learn','sklearn'], 'MS SQL Server':['ms sql server','mssql'],
  '1C':['1c','1с'], 'REST API':['rest api','rest'], 'Hugging Face':['hugging face','huggingface'],
  'GitLab CI/CD':['gitlab ci/cd','gitlab ci'], 'Tailwind CSS':['tailwind css','tailwind'],
  'Machine Learning':['machine learning','машинное обучение'],
};
const word = /[\p{L}\p{N}_+#]/u;
const ambiguous = new Set(['Go','R','C']);
const escape = s => s.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');
export function extractSkills(text) {
  if (typeof text !== 'string') throw new TypeError('Ожидается текст резюме');
  const entities = [], excluded = [];
  for (const item of technologyCatalog) {
    const skill = item.technology;
    for (const alias of new Set([skill, ...(aliases[skill] || [])].map(s=>s.toLowerCase()))) {
      const pattern = escape(alias).replace(/\s+/g, '[\\s\\u00a0]+');
      for (const match of text.matchAll(new RegExp(pattern, 'giu'))) {
        const start=match.index, end=start+match[0].length;
        if ((start && word.test(text[start-1])) || (end<text.length && word.test(text[end]))) continue;
        const lineStart = text.lastIndexOf('\n',start-1)+1;
        const lineEnd = text.indexOf('\n',end);
        const line = text.slice(lineStart,lineEnd<0?text.length:lineEnd);
        const context = text.slice(Math.max(lineStart,start-90),start);
        const section = /(?:навыки|технологии|стек|skills|technologies|stack)\s*[:—-]?/i.test(line);
        const language = /(?:язык|language|программирован|разработ|developer|опыт|experience|использ|using|работ|golang)/i.test(line);
        const list = /[,;|]/.test(line) && /python|java|sql|rust|kotlin|c\+\+|javascript|typescript/i.test(line);
        if(ambiguous.has(skill) && alias.toLowerCase()===skill.toLowerCase() && !(section||language||list)) continue;
        const negated = /(?:не\s+(?:знаю|использую|владею|работал[аи]?\s+с)|без\s+опыта(?:\s+(?:с|в))?|no\s+experience(?:\s+(?:with|in))?|never\s+used)\s*$/i.test(context);
        const entity={skill,label:'SKILL',text:match[0],start,end,categories:item.categories,testRequired:item.testRequired,evidence:section?'skills-section':'mention'};
        (negated?excluded:entities).push(entity);
      }
    }
  }
  entities.sort((a,b)=>a.start-b.start||b.end-a.end);
  const unique=entities.filter((e,i,all)=>!all.slice(0,i).some(p=>p.start<=e.start&&p.end>=e.end));
  return {engine:'heuristics-v2',skills:[...new Set(unique.map(e=>e.skill))],entities:unique,excluded};
}
export function mergeSkills(current,found) {
  const seen=new Set();
  return [...current,...found].filter(s=>{const key=s.toLowerCase();if(seen.has(key))return false;seen.add(key);return true});
}
export function validateResume(file) {
  if(!file||!/\.(pdf|txt)$/i.test(file.name))throw new Error('Выберите PDF или TXT');
  if(file.size>5*1024*1024)throw new Error('Максимальный размер — 5 МБ');
}
export function resolveTechnology(value){
 const key=value.trim().toLowerCase();
 return technologyCatalog.find(item=>[item.technology,...(aliases[item.technology]||[])].some(s=>s.toLowerCase()===key))?.technology||value.trim();
}
export function suggestTechnologies(query,selected=[],category=''){
 const key=query.trim().toLowerCase(), used=new Set(selected.map(s=>s.toLowerCase()));
 if(!key)return [];
 return technologyCatalog.filter(item=>!used.has(item.technology.toLowerCase())&&[item.technology,...(aliases[item.technology]||[])].some(s=>s.toLowerCase().includes(key)))
 .sort((a,b)=>{
  const score=x=>(x.technology.toLowerCase()===key?4:0)+(x.technology.toLowerCase().startsWith(key)?2:0)+(x.categories.includes(category)?1:0);
  return score(b)-score(a)||a.technology.localeCompare(b.technology);
 }).slice(0,12);
}
