import test from 'node:test';
import assert from 'node:assert/strict';
import {extractSkills,mergeSkills,validateResume} from '../src/ner.js';
test('Russian resume aliases and canonical skills',()=>assert.deepEqual(extractSkills('Питон, postgres, докер и react.js').skills,['Python','PostgreSQL','Docker','React']));
test('No substring matches or ambiguous Go',()=>assert.deepEqual(extractSkills('JavaScript github reaction go Google').skills,['JavaScript','GitHub']));
test('C languages and punctuation',()=>assert.deepEqual(extractSkills('C++, C#; SQL.').skills,['C++','C#','SQL']));
test('No duplicates; offsets point at original text',()=>{const text='PYTHON python';const r=extractSkills(text);assert.deepEqual(r.skills,['Python']);for(const e of r.entities)assert.equal(text.slice(e.start,e.end),e.text)});
test('Empty and invalid text',()=>{assert.deepEqual(extractSkills('').skills,[]);assert.throws(()=>extractSkills(null),TypeError)});
test('Merge preserves existing and deduplicates case',()=>assert.deepEqual(mergeSkills(['Python'],['python','Docker']),['Python','Docker']));
test('Validate type and size',()=>{validateResume({name:'cv.PDF',size:100});assert.throws(()=>validateResume({name:'cv.exe',size:1}));assert.throws(()=>validateResume({name:'cv.pdf',size:6*1024*1024}))});
test('CSV catalog covers uncommon technologies',()=>assert.deepEqual(extractSkills('Scala, Burp Suite, Apache Iceberg, Zabbix').skills,['Scala','Burp Suite','Apache Iceberg','Zabbix']));
test('Longer names win without nested duplicates',()=>assert.deepEqual(extractSkills('Spring Boot, React Native, Docker Compose').skills,['Spring Boot','React Native','Docker Compose']));
test('Multi-line aliases and technology versions',()=>assert.deepEqual(extractSkills('Power\nBI; Python 3.12; postgres 16; sklearn').skills,['Power BI','Python','PostgreSQL','scikit-learn']));
test('Exclude direct negations while retaining positive skills',()=>{const r=extractSkills('Не знаю Docker\nSkills: Python\nno experience with Kubernetes');assert.deepEqual(r.skills,['Python']);assert.deepEqual(r.excluded.map(e=>e.skill),['Docker','Kubernetes'])});
test('Ambiguous languages require context',()=>{assert.deepEqual(extractSkills('Go to work. R and C.').skills,[]);assert.deepEqual(extractSkills('Стек: Go, R, C').skills,['Go','R','C'])});
test('Resume abbreviations resolve only to canonical catalog terms',()=>{
 const found=extractSkills('Технический стек: ML / DL / LLM / RAG').skills;
 assert.deepEqual(found,['Machine Learning','Deep Learning','Generative AI / LLM','Search / Retrieval']);
});
