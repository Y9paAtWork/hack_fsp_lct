const startSection=/^(?:опыт(?:\s+работы)?|профессиональный\s+опыт|трудовая\s+деятельность|work\s+experience|employment(?:\s+history)?|professional\s+experience|experience)\s*(?:[:—-]\s*\d+.*)?[:—-]?$/i;
const endSection=/^(?:образование|education|навыки|skills|ключевые\s+навыки|технологии|стек|проекты|projects|сертификаты|certifications|о\s+себе|about(?:\s+me)?|языки|languages)\s*[:—-]?$/i;
const month='(?:январ[ья]|феврал[ья]|март[а]?|апрел[ья]|ма[йя]|июн[ья]|июл[ья]|август[а]?|сентябр[ья]|октябр[ья]|ноябр[ья]|декабр[ья]|jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)';
const date=`(?:(?:${month})\\.?\\s+|(?:0?[1-9]|1[0-2])[./])?(?:19|20)\\d{2}`;
const range=new RegExp(`(${date})\\s*(?:[-–—]|\\bto\\b|\\bпо\\b)\\s*(${date}|настоящее\\s+время|н[.]?\\s*в[.]?|по\\s+настоящее\\s+время|present|current|now|сейчас)`, 'i');
const rolePattern=/разработчик|инженер|аналитик|программист|руководитель|тестировщик|специалист|developer|engineer|analyst|manager|scientist|designer|intern|стаж[её]р/i;
const companyPattern=/^(?:компания|company|работодатель|employer)\s*[:—-]|(?:^(?:ООО|АО|ПАО|ОАО|ЗАО)\s|\b(?:Inc|Ltd|LLC|GmbH)\b)/i;
const headerLike=line=>line&&line.length<100&&!/^[•*—-]|разрабатывал|разработал|разработка|поддержка|проектирование|создал|создала|implemented|developed|built|maintained|responsibilities|обязанности|достижения/i.test(line);
const stripLabel=line=>line.replace(/^(?:компания|company|работодатель|employer|должность|position|role)\s*[:—-]\s*/i,'').trim();
export function extractExperience(text){
 if(typeof text!=='string')throw new TypeError('Ожидается текст резюме');
 const lines=text.split(/\r?\n/).map(s=>s.trim());
 const sections=[];let current=null;
 for(const line of lines){
  if(startSection.test(line)){if(current)sections.push(current);current=[];continue;}
  if(endSection.test(line)){if(current)sections.push(current);current=null;continue;}
  if(current)current.push(line);
 }
 if(current)sections.push(current);
 const jobs=[];
 for(const section of sections){
  const dates=section.map((line,i)=>({line,i,match:range.exec(line)})).filter(x=>x.match&&x.line.length<=130);
  const boundaries=dates.map((d,index)=>{
   let start=d.i;
   if(index===0||d.i>dates[index-1].i+2){
    for(let n=1;n<=2&&d.i-n>=0;n++){
     const line=section[d.i-n];
     if(!headerLike(line))break;
     if(rolePattern.test(line)||companyPattern.test(line)||n===1){start=d.i-n;}else break;
    }
   }
   return start;
  });
  for(let j=0;j<dates.length;j++){
   const d=dates[j], block=section.slice(boundaries[j],j+1<dates.length?boundaries[j+1]:section.length).filter(Boolean);
   const withoutDate=block.map(line=>line===d.line?line.replace(d.match[0],'').replace(/^[\s|,;—–-]+|[\s|,;—–-]+$/g,''):line).filter(line=>line&&!/^\d+\s*(?:год|лет|месяц|year|month)/i.test(line));
   let company='',role='';const used=new Set();
   for(let i=0;i<withoutDate.length;i++){
    const line=withoutDate[i];
    if(!company&&companyPattern.test(line)){company=stripLabel(line);used.add(i);}
    else if(!role&&headerLike(line)&&rolePattern.test(line)){role=stripLabel(line);used.add(i);}
   }
   if(!company){const i=withoutDate.findIndex((line,i)=>!used.has(i)&&headerLike(line));if(i>=0){company=stripLabel(withoutDate[i]);used.add(i);}}
   const description=withoutDate.filter((_,i)=>!used.has(i)).join('\n');
   jobs.push({company,role,start:d.match[1],end:/present|current|now|сейчас|настоящее|н[.]?\s*в/i.test(d.match[2])?'По настоящее время':d.match[2],description,source:block.join('\n')});
  }
 }
 return jobs;
}
export function mergeExperience(existing,found){
 const key=j=>[j.company,j.start,j.end].map(s=>(s||'').trim().toLowerCase()).join('|');
 const seen=new Set(existing.map(key));
 return [...existing,...found.filter(j=>{const k=key(j);if(seen.has(k))return false;seen.add(k);return true;})];
}
