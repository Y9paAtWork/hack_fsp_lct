import test from 'node:test';
import assert from 'node:assert/strict';
import {extractExperience,mergeExperience} from '../src/experience.js';
import {suggestTechnologies,resolveTechnology} from '../src/ner.js';
import {saveProfile,loadProfile,emptyProfile} from '../src/profileStore.js';
test('Russian experience with company before period and current job',()=>{
 const text='Опыт работы\nООО Альфа\nBackend Developer\nМарт 2022 — настоящее время\n• Разработка API на Python\n\nОбразование\n2020 — 2024\nУниверситет';
 const jobs=extractExperience(text);assert.equal(jobs.length,1);assert.equal(jobs[0].company,'ООО Альфа');assert.equal(jobs[0].role,'Backend Developer');assert.equal(jobs[0].start,'Март 2022');assert.equal(jobs[0].end,'По настоящее время');assert.match(jobs[0].description,/API/);
});
test('English date-first experience',()=>{
 const jobs=extractExperience('Work Experience\nJan 2021 - Dec 2023\nCompany: Acme Inc\nSoftware Engineer\n• Built services\n\n2024 - Present\nCompany: Beta Ltd\nBackend Developer\n• Maintained APIs\nSkills\nPython');
 assert.equal(jobs.length,2);assert.equal(jobs[1].company,'Beta Ltd');assert.equal(jobs[0].role,'Software Engineer');assert.equal(jobs[0].description,'• Built services');
});
test('Two jobs with headers before dates remain separate',()=>{
 const jobs=extractExperience('Experience\nAcme Inc\nSoftware Engineer\n2020 - 2022\n• Built APIs\n\nBeta Ltd\nBackend Developer\n2023 - 2025\n• Developed apps');
 assert.equal(jobs.length,2);assert.equal(jobs[1].company,'Beta Ltd');assert.equal(jobs[0].description,'• Built APIs');
});
test('Numeric month periods and missing role preserve uncertainty',()=>{
 const jobs=extractExperience('Опыт работы\nCompany: Acme\n03.2022 – 11.2024\n• Работа с API');assert.equal(jobs[0].start,'03.2022');assert.equal(jobs[0].role,'');
});
test('Dates outside experience section do not become jobs',()=>{assert.deepEqual(extractExperience('Образование\n2020 - 2024\nУниверситет'),[]);assert.deepEqual(extractExperience('Навыки: Python'),[])});
test('Repeated import preserves manual entries and avoids duplicates',()=>{const j={company:'Acme',start:'2020',end:'2024',description:'Manual'};assert.deepEqual(mergeExperience([j],[{...j,description:'Parsed'}]),[j])});
test('Experience position and original evidence survive storage',()=>{
 const m=new Map(),s={getItem:k=>m.get(k),setItem:(k,v)=>m.set(k,v)};const j={company:'Acme',role:'Engineer',start:'2020',end:'2024',description:'API',source:'Original'};
 saveProfile(s,{...emptyProfile(),selected:['Backend'],jobs:{Backend:[j]}});assert.deepEqual(loadProfile(s).jobs.Backend[0],j);
});
test('Autocomplete uses CSV, supports aliases, excludes existing skills',()=>{
 assert.equal(resolveTechnology('postgres'),'PostgreSQL');assert.equal(resolveTechnology('PYTHON'),'Python');assert.equal(resolveTechnology('Custom Tool'),'Custom Tool');
 assert.ok(suggestTechnologies('post').some(s=>s.technology==='PostgreSQL'));assert.ok(suggestTechnologies('пит').some(s=>s.technology==='Python'));assert.ok(!suggestTechnologies('py',['Python']).some(s=>s.technology==='Python'));assert.deepEqual(suggestTechnologies(''),[]);
});
test('Autocomplete returns related names in canonical form',()=>{const r=suggestTechnologies('react');assert.deepEqual(r.map(x=>x.technology),['React','React Native']);assert.equal(suggestTechnologies('sql').length<=12,true)});
test('Missing company does not turn duties into a company',()=>{const jobs=extractExperience('Опыт работы\n2023 — сейчас\nРазработка API\nПоддержка сервисов');assert.equal(jobs[0].company,'');assert.equal(jobs[0].end,'По настоящее время');assert.match(jobs[0].description,/Разработка API/)});
