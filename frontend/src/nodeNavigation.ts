export const siteLinks = [
  {href:'/network',label:'节点网络'},
  {href:'/connect',label:'连接启动器'},
  {href:'/developers',label:'开发文档'},
  {href:'/feedback',label:'问题反馈'},
  {href:'/account',label:'用户中心',id:'open-account'},
] as const;

export const adminSections = [
  {id:'nodes',label:'节点管理',href:'/admin/nodes',items:[
    {href:'/admin/nodes',label:'节点列表'},
    {href:'/admin/network',label:'连接网络'},
    {href:'/admin/settings',label:'节点设置'},
  ]},
  {id:'users',label:'用户与支持',href:'/admin/users',items:[
    {href:'/admin/users',label:'用户列表'},
    {href:'/admin/players',label:'在线玩家'},
    {href:'/admin/feedback',label:'反馈管理'},
    {href:'/admin/diagnostics',label:'连接诊断'},
  ]},
  {id:'operations',label:'运维',href:'/admin/mail',items:[
    {href:'/admin/mail',label:'邮件服务'},
    {href:'/admin/history',label:'操作记录'},
  ]},
] as const;

export function adminSectionFor(path:string) {
  if(['/admin/users','/admin/players','/admin/feedback','/admin/diagnostics'].includes(path))return 'users';
  if(['/admin/mail','/admin/history'].includes(path))return 'operations';
  return 'nodes';
}

export function itemActive(path:string,href:string) {
  return path===href||(path==='/admin'&&href==='/admin/nodes')||
    (href==='/account'&&path.startsWith('/account/'));
}
