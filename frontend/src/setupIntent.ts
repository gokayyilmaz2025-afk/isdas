export type SetupIntent={kind:'personal'|'business'|'agency',goal:string};
const kinds=['personal','business','agency'],goals=['planning','content','research','sales','support','documents'];
export function setupIntent(search=location.search):SetupIntent|null{
 const params=new URLSearchParams(search),kind=params.get('for')||'',goal=params.get('goal')||'';
 if(!kinds.includes(kind))return null;
 return {kind:kind as SetupIntent['kind'],goal:goals.includes(goal)?goal:kind==='personal'?'planning':'content'};
}
export function setupLink(kind:string,goal:string){return '/app?'+new URLSearchParams({for:kind,goal}).toString()}
