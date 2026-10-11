import React from 'react';
import {describe,it,expect,beforeEach,afterEach,vi} from 'vitest';
import {render,screen,fireEvent,waitFor,cleanup} from '@testing-library/react';
import {AuthProvider} from '../../src/AuthContext';
import {ProfileProvider} from '../../src/ProfileContext';
import {App} from '../../src/main';
import TechnologyInput from '../../src/TechnologyInput';

const empty=()=>({version:1,name:'Nika',email:'n@example.org',fsp:'',terms:true,step:1,selected:[],active:'',stacks:{},jobs:{},ownProjects:{},quiz:null});
const storage=()=>{const values=new Map();return {getItem:key=>values.get(key)??null,setItem:(key,value)=>values.set(key,String(value)),removeItem:key=>values.delete(key),clear:()=>values.clear()};};
let user,profile,revision,requests;
beforeEach(()=>{
 Object.defineProperty(window,'localStorage',{configurable:true,value:storage()});
 Object.defineProperty(window,'sessionStorage',{configurable:true,value:storage()});
 Object.defineProperty(globalThis,'localStorage',{configurable:true,value:window.localStorage});
 cleanup();window.localStorage.clear();window.sessionStorage.clear();window.localStorage.setItem('fsp.candidate.v1',JSON.stringify({...empty(),name:'Legacy',step:4}));
 user=null;profile=empty();revision=0;requests=[];window.location.hash='/candidate/register';window.scrollTo=vi.fn();window.confirm=vi.fn(()=>true);
 global.fetch=vi.fn(async(path,options={})=>{
  const body=options.body?JSON.parse(options.body):null;requests.push({path,body,method:options.method});let status=200,data={};
  if(path==='/api/auth/me'){status=user?200:401;data=user?{user}:{detail:'Not signed in'};}
  else if(path==='/api/auth/register'){user={id:'new-user',email:body.email,name:body.name,role:body.role};profile=body.role==='candidate'?body.profile:{...empty(),name:body.name,email:body.email};data={user};}
  else if(path==='/api/profile'&&options.method==='PUT'){profile=body.profile;revision++;data={profile,revision};}
  else if(path==='/api/profile'){data={profile,revision};}
  else if(path==='/api/profile/reset'){profile=empty();revision++;data={profile,revision};}
  else if(path==='/api/auth/logout'){user=null;data={ok:true};}
  else if(path==='/api/profile/items'){data={items:[]};}
  else if(path==='/api/grades/declarations'){data={grades:{}};}
  else if(path==='/api/activity/calendar'){data={days:[]};}
  return {ok:status<400,status,json:async()=>data};
 });
});
afterEach(()=>{cleanup();vi.restoreAllMocks();});
function start(){return render(<AuthProvider><ProfileProvider><App/></ProfileProvider></AuthProvider>)}

describe('Account UI',()=>{
 it('ignores legacy draft and registers a new candidate',async()=>{
  start();await screen.findByRole('heading',{name:'СОЗДАНИЕ ПРОФИЛЯ'});
  expect(screen.queryByDisplayValue('Legacy')).toBeNull();
  fireEvent.change(screen.getByLabelText('Имя'),{target:{value:'New Nika'}});
  fireEvent.change(screen.getByLabelText('Email'),{target:{value:'new@example.org'}});
  fireEvent.change(screen.getByLabelText(/Пароль/),{target:{value:'secure-password'}});
  fireEvent.change(screen.getByLabelText('FSP ID'),{target:{value:'fsp-123'}});
  fireEvent.change(screen.getByLabelText('Минимальная зарплата, ₽/мес'),{target:{value:'120000'}});
  fireEvent.click(screen.getByLabelText('Я принимаю условия использования сервиса'));
  fireEvent.click(screen.getByText('Продолжить'));
  await screen.findByText('ВАШИ СПЕЦИАЛИЗАЦИИ');
  expect(requests.some(r=>r.path==='/api/auth/register')).toBe(false);
  fireEvent.click(screen.getByRole('button',{name:/Backend/}));
  fireEvent.click(screen.getByRole('button',{name:/Frontend/}));
  fireEvent.change(screen.getByLabelText('Грейд — Backend'),{target:{value:'Middle'}});
  fireEvent.change(screen.getByLabelText('Грейд — Frontend'),{target:{value:'Junior'}});
  fireEvent.click(screen.getByText('Продолжить'));
  await screen.findByText('СОБЕРИТЕ СВОЙ СТЕК');
  expect(requests.some(r=>r.path==='/api/auth/register')).toBe(false);
  fireEvent.change(screen.getByRole('combobox'),{target:{value:'Python'}});
  fireEvent.click(screen.getByRole('button',{name:'Добавить технологию'}));
  fireEvent.click(screen.getByText('Продолжить'));
  await screen.findByText('РАССКАЖИТЕ О СВОЕЙ РАБОТЕ');
  expect(requests.some(r=>r.path==='/api/auth/register')).toBe(false);
  fireEvent.click(screen.getByRole('button',{name:'Добавить опыт работы'}));
  fireEvent.change(screen.getByLabelText('Резюме для работы Backend 1'),{target:{value:'Frontend'}});
  fireEvent.change(screen.getByLabelText('Компания'),{target:{value:'Frontend Studio'}});
  fireEvent.click(screen.getByRole('button',{name:'Добавить проект'}));
  fireEvent.change(screen.getByLabelText('Название проекта'),{target:{value:'Backend API'}});
  fireEvent.click(screen.getByText('Сохранить и открыть кабинет'));
  await waitFor(()=>expect(requests.find(r=>r.path==='/api/auth/register')?.body.specialization_grades).toEqual({Backend:'Middle',Frontend:'Junior'}));
  expect(requests.find(r=>r.path==='/api/auth/register')?.body.profile.fsp).toBe('fsp-123');
  expect(requests.find(r=>r.path==='/api/auth/register')?.body.profile.salaryMin).toBe(120000);
  expect(requests.find(r=>r.path==='/api/auth/register')?.body.profile.stacks).toEqual({Backend:['Python'],Frontend:['Python']});
  expect(requests.find(r=>r.path==='/api/auth/register')?.body.profile.jobs.Frontend[0].company).toBe('Frontend Studio');
  expect(requests.find(r=>r.path==='/api/auth/register')?.body.profile.ownProjects.Backend[0].name).toBe('Backend API');
  await screen.findByRole('heading',{name:/ДОБРЫЙ ДЕНЬ/});
  expect(screen.getByText('Backend / Frontend')).toBeTruthy();
 });
 it('shows vacancy search and applications without profile settings',async()=>{
  user={id:'user-1',email:'n@example.org',name:'Nika',role:'candidate'};
  profile={...empty(),step:4,selected:['Backend'],active:'Backend',stacks:{Backend:['Python']}};
  window.location.hash='/candidate/home';start();
  await screen.findByRole('heading',{name:/ДОБРЫЙ ДЕНЬ/});
  expect(screen.queryByText('Настройки')).toBeNull();
  expect(screen.getByText('Открыть вакансии')).toBeTruthy();
  expect(screen.getByRole('button',{name:'Открыть тесты'})).toBeTruthy();
  expect(screen.queryByText('Мои отклики')).toBeNull();
 });
 it('edits saved stack and work from the candidate cabinet',async()=>{
  user={id:'user-1',email:'n@example.org',name:'Nika',role:'candidate'};
  profile={...empty(),step:4,selected:['Backend'],active:'Backend',stacks:{Backend:['Python']},jobs:{Backend:[{company:'Old company',role:'Dev',start:'2020',end:'2024',description:'API'}]}};
  window.location.hash='/candidate/edit';start();
  await screen.findByDisplayValue('Old company');
  fireEvent.click(screen.getByLabelText('Удалить Python'));
  fireEvent.change(screen.getByDisplayValue('Old company'),{target:{value:'New company'}});
  await waitFor(()=>expect(requests.some(r=>r.path==='/api/profile'&&r.method==='PUT'&&r.body.profile.jobs.Backend[0].company==='New company'&&r.body.profile.stacks.Backend.length===0)).toBe(true));
 });
 it('employer registration keeps company fields, consent and password confirmation',async()=>{
  window.location.hash='/employer/register';start();
  await screen.findByRole('heading',{name:'РЕГИСТРАЦИЯ РАБОТОДАТЕЛЯ'});
  fireEvent.change(screen.getByLabelText('Название компании'),{target:{value:'Компания А'}});
  fireEvent.change(screen.getByLabelText('Рабочая почта'),{target:{value:'team@example.org'}});
  fireEvent.change(screen.getByLabelText('Пароль'),{target:{value:'secure-password'}});
  fireEvent.change(screen.getByLabelText('Повторите пароль'),{target:{value:'different-password'}});
  fireEvent.click(screen.getByLabelText('Я принимаю условия использования сервиса'));
  fireEvent.click(screen.getByText('Зарегистрироваться'));
  expect(screen.getByRole('alert').textContent).toContain('Пароли не совпадают');
  expect(requests.some(r=>r.path==='/api/auth/register')).toBe(false);
  fireEvent.change(screen.getByLabelText('Повторите пароль'),{target:{value:'secure-password'}});
  fireEvent.click(screen.getByText('Зарегистрироваться'));
  await waitFor(()=>expect(requests.find(r=>r.path==='/api/auth/register')?.body.company_name).toBe('Компания А'));
 });
 it('skill suggestions add canonical CSV value using keyboard',()=>{
  const add=vi.fn();render(<TechnologyInput selected={[]} category="Backend" onAdd={add}/>);
  const input=screen.getByRole('combobox');fireEvent.change(input,{target:{value:'postgres'}});expect(screen.getByRole('option').textContent).toContain('PostgreSQL');
  fireEvent.keyDown(input,{key:'ArrowDown'});fireEvent.keyDown(input,{key:'Enter'});expect(add).toHaveBeenCalledWith('PostgreSQL');expect(input.value).toBe('');
 });
});
