import React from 'react';
import {describe,it,expect,beforeEach,afterEach,vi} from 'vitest';
import {render,screen,fireEvent,waitFor,cleanup} from '@testing-library/react';
import {AuthProvider} from '../../src/AuthContext';
import {ProfileProvider} from '../../src/ProfileContext';
import {DemoProvider} from '../../src/demoStore';
import {App} from '../../src/main';
import TechnologyInput from '../../src/TechnologyInput';

const empty=()=>({version:1,name:'Nika',email:'n@example.org',fsp:'',terms:true,step:1,selected:[],active:'',stacks:{},jobs:{},ownProjects:{},quiz:null});
let user,profile,revision,requests;
beforeEach(()=>{
 cleanup();localStorage.clear();localStorage.setItem('fsp.candidate.v1',JSON.stringify({...empty(),name:'Legacy',step:4}));
 user=null;profile=empty();revision=0;requests=[];window.location.hash='/candidate/register';window.scrollTo=vi.fn();window.confirm=vi.fn(()=>true);
 global.fetch=vi.fn(async(path,options={})=>{
  const body=options.body?JSON.parse(options.body):null;requests.push({path,body,method:options.method});let status=200,data={};
  if(path==='/api/auth/me'){status=user?200:401;data=user?{user}:{detail:'Not signed in'};}
  else if(path==='/api/auth/register'){user={id:'new-user',email:body.email,name:body.name,role:body.role};profile={...empty(),name:body.name,email:body.email};data={user};}
  else if(path==='/api/profile'&&options.method==='PUT'){profile=body.profile;revision++;data={profile,revision};}
  else if(path==='/api/profile'){data={profile,revision};}
  else if(path==='/api/profile/reset'){profile=empty();revision++;data={profile,revision};}
  else if(path==='/api/auth/logout'){user=null;data={ok:true};}
  else if(path==='/api/profile/items'){data={items:[]};}
  return {ok:status<400,status,json:async()=>data};
 });
});
afterEach(()=>{cleanup();vi.restoreAllMocks();});
function start(){return render(<AuthProvider><ProfileProvider><DemoProvider><App/></DemoProvider></ProfileProvider></AuthProvider>)}

describe('Account UI',()=>{
 it('ignores legacy draft and registers a new candidate',async()=>{
  start();await screen.findByText('РЕГИСТРАЦИЯ КАНДИДАТА');
  expect(screen.queryByDisplayValue('Legacy')).toBeNull();
  fireEvent.change(screen.getByLabelText('Имя'),{target:{value:'New Nika'}});
  fireEvent.change(screen.getByLabelText('Email'),{target:{value:'new@example.org'}});
  fireEvent.change(screen.getByLabelText(/Пароль/),{target:{value:'secure-password'}});
  fireEvent.click(screen.getByText('Создать аккаунт'));
  await screen.findByText('ОСНОВНЫЕ ДАННЫЕ');expect(screen.getByDisplayValue('New Nika')).toBeTruthy();expect(requests.some(r=>r.path==='/api/auth/register')).toBe(true);
 });
 it('reset remounts the wizard with cleared experience and first step',async()=>{
  user={id:'user-1',email:'n@example.org',name:'Nika',role:'candidate'};
  profile={...empty(),step:4,selected:['Backend'],active:'Backend',stacks:{Backend:['Python']},jobs:{Backend:[{company:'Old company',role:'Dev',start:'2020',end:'2024',description:'API'}]}};
  start();await screen.findByDisplayValue('Old company');fireEvent.click(screen.getByText('Начать анкету заново'));
  await screen.findByText('ОСНОВНЫЕ ДАННЫЕ');expect(screen.queryByDisplayValue('Old company')).toBeNull();expect(profile.jobs).toEqual({});expect(profile.selected).toEqual([]);
  await waitFor(()=>expect(requests.some(r=>r.path==='/api/profile/reset')).toBe(true));
 });
 it('skill suggestions add canonical CSV value using keyboard',()=>{
  const add=vi.fn();render(<TechnologyInput selected={[]} category="Backend" onAdd={add}/>);
  const input=screen.getByRole('combobox');fireEvent.change(input,{target:{value:'postgres'}});expect(screen.getByRole('option').textContent).toContain('PostgreSQL');
  fireEvent.keyDown(input,{key:'ArrowDown'});fireEvent.keyDown(input,{key:'Enter'});expect(add).toHaveBeenCalledWith('PostgreSQL');expect(input.value).toBe('');
 });
});
