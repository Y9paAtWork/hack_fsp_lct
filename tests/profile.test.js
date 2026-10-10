import test from 'node:test';
import assert from 'node:assert/strict';
import {emptyProfile,saveProfile,loadProfile,deleteProfile,profileSkills} from '../src/profileStore.js';
const storage=()=>{const m=new Map();return {getItem:k=>m.get(k)||null,setItem:(k,v)=>m.set(k,v),removeItem:k=>m.delete(k)}};
test('Profile round-trip preserves registration and quiz, excludes password',()=>{
 const s=storage(),p={...emptyProfile(),name:'Ника',email:'nika@example.org',selected:['Backend'],active:'Backend',step:4,stacks:{Backend:['Python']},jobs:{Backend:[{company:'A',start:'2025',end:'2026',description:'API'}]},ownProjects:{Backend:[{name:'Сайт',description:'HR',tasks:'Парсер'}]},quiz:{correct:1,total:2,score:50,date:'2026'},password:'secret'};
 const saved=saveProfile(s,p);assert.deepEqual(loadProfile(s),saved);assert.equal('password' in saved,false);assert.equal(saved.jobs.Backend[0].company,'A');assert.equal(saved.ownProjects.Backend[0].name,'Сайт');assert.equal(saved.quiz.score,50);
});
test('Corrupt storage recovers and malformed values are sanitized',()=>{
 const s=storage();s.setItem('fsp.candidate.v1','broken');assert.deepEqual(loadProfile(s),emptyProfile());
 const p=saveProfile(s,{name:123,selected:['Backend',null],stacks:{Backend:['Python',null]},jobs:{Backend:[null,{company:'A',secret:'x'}]}});assert.equal(p.name,'');assert.deepEqual(p.selected,['Backend']);assert.deepEqual(p.stacks.Backend,['Python']);assert.equal('secret' in p.jobs.Backend[0],false);
});
test('Only selected specializations contribute skills',()=>assert.deepEqual(profileSkills({...emptyProfile(),selected:['Backend'],stacks:{Backend:['Python','Python'],Frontend:['React']}}),['Python']));
test('Delete clears saved profile',()=>{const s=storage();saveProfile(s,{name:'Nika'});deleteProfile(s);assert.deepEqual(loadProfile(s),emptyProfile())});
test('Write failure is propagated',()=>assert.throws(()=>saveProfile({setItem:()=>{throw new Error('quota')}},{name:'Nika'}),/quota/));
