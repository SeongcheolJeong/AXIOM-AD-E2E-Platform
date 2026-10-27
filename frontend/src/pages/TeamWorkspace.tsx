import {useState} from 'react';
import {api,useResource} from '../api';
import {ErrorState,Loading} from '../components/ui';

type Access={mode:string;project_id:string|null;role:string;user_label:string;legacy_domains_available:boolean;permissions:{admin:boolean}};
type Project={id:string;name:string};
type Token={id:string;user_label:string;role:string;revoked_at:string|null};

export default function TeamWorkspace(){
  const access=useResource<Access>('/team/whoami'),projects=useResource<{items:Project[]}>('/projects');
  const [selected,setSelected]=useState(''),[name,setName]=useState(''),[user,setUser]=useState(''),[role,setRole]=useState('Viewer');
  const [issued,setIssued]=useState(''),[error,setError]=useState(''),[busy,setBusy]=useState(false);
  const project=selected||access.data?.project_id||projects.data?.items[0]?.id||'';
  const tokens=useResource<{items:Token[]}>(access.data?.permissions.admin&&project?`/projects/${encodeURIComponent(project)}/tokens`:null);
  async function mutate(fn:()=>Promise<unknown>){setBusy(true);setError('');try{await fn();projects.reload();tokens.reload();}catch(e){setError((e as Error).message);}finally{setBusy(false);}}
  if(access.error||projects.error)return <ErrorState message={access.error||projects.error} retry={()=>{access.reload();projects.reload();}}/>;
  if(!access.data||!projects.data)return <Loading label="Reading project access…"/>;
  return <div className="expansion-workspace">
    <div className="page-heading"><div><span className="eyebrow">ADP · PROJECT ACCESS</span><h1>Team workspace</h1><p>{access.data.user_label} · {access.data.role}</p></div></div>
    <div className="evidence-note">Project tokens cover experiments, Scenario v2, sequences, replay specifications and their evidence. Existing data and validation APIs remain in the operator workspace until their project ownership is migrated.</div>
    <section className="panel"><h2>Project</h2><label>Selected project<select value={project} onChange={e=>{setSelected(e.target.value);setIssued('');}}>{projects.data.items.map(p=><option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
      {access.data.mode!=='project'&&<div className="expansion-controls"><input aria-label="New project name" placeholder="Project name" value={name} onChange={e=>setName(e.target.value)}/><button className="button secondary" disabled={busy||!name.trim()} onClick={()=>mutate(async()=>{const p=await api<Project>('/projects',{method:'POST',body:JSON.stringify({name})});setSelected(p.id);setName('');})}>Create project</button></div>}
    </section>
    {access.data.permissions.admin&&<section className="panel"><h2>Project access tokens</h2><p>Each token has one project and role. The secret is shown once; it is stored hashed on the server.</p>
      <div className="expansion-controls"><input aria-label="Token user label" placeholder="User label" value={user} onChange={e=>setUser(e.target.value)}/><select aria-label="Token role" value={role} onChange={e=>setRole(e.target.value)}>{['Viewer','Engineer','Reviewer','Admin'].map(r=><option key={r}>{r}</option>)}</select><button className="button primary" disabled={busy||!project||!user.trim()} onClick={()=>mutate(async()=>{const value=await api<{token:string}>(`/projects/${encodeURIComponent(project)}/tokens`,{method:'POST',body:JSON.stringify({user_label:user,role})});setIssued(value.token);setUser('');})}>Issue token</button></div>
      {issued&&<div className="form-note"><label>New token · shown once<input type="password" readOnly value={issued}/></label><button className="button secondary" onClick={()=>navigator.clipboard.writeText(issued)}>Copy token</button><button className="button secondary" onClick={()=>setIssued('')}>Dismiss secret</button></div>}
      {tokens.error&&<p role="alert">{tokens.error}</p>}
      <table><thead><tr><th>User</th><th>Role</th><th>Status</th><th>Action</th></tr></thead><tbody>{tokens.data?.items.map(t=><tr key={t.id}><td>{t.user_label}</td><td>{t.role}</td><td>{t.revoked_at?'Revoked':'Active'}</td><td><button className="button secondary" disabled={busy||!!t.revoked_at} onClick={()=>mutate(()=>api(`/projects/${encodeURIComponent(project)}/tokens/${encodeURIComponent(t.id)}/revoke`,{method:'POST'}))}>Revoke</button></td></tr>)}</tbody></table>
    </section>}
    {error&&<p className="inline-error" role="alert">{error}</p>}
  </div>;
}
