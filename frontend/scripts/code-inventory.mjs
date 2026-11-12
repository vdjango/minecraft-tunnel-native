// Source AST inventory. Build outputs under site/ and web/ are not JS owners.
import {readFileSync, readdirSync} from 'node:fs';
import {resolve, relative} from 'node:path';
import ts from 'typescript';
import {parse as parseSfc} from '@vue/compiler-sfc';
import {parse as parseTemplate} from '@vue/compiler-dom';

const root=resolve(import.meta.dirname,'../..');
const methods=[], dom=[], selectors=[], files=[];
function script(source,path,offset=0) {
  const ast=ts.createSourceFile(path,source,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
  const line=node=>source.slice(0,node.getStart(ast)).split('\n').length+offset;
  function visit(node) {
    if(ts.isFunctionDeclaration(node)||ts.isMethodDeclaration(node)||ts.isArrowFunction(node)||ts.isFunctionExpression(node)||ts.isConstructorDeclaration(node)||ts.isGetAccessorDeclaration(node)||ts.isSetAccessorDeclaration(node)) {
      const name=node.name?.getText(ast)||node.parent?.name?.getText(ast)||`<callback@${line(node)}>`;
      methods.push({path,line:line(node),kind:ts.SyntaxKind[node.kind],symbol:name,parameters:node.parameters.map(p=>p.getText(ast)).join(', ')});
    }
    if(ts.isCallExpression(node)&&ts.isPropertyAccessExpression(node.expression)&&['getElementById','querySelector','querySelectorAll','closest','addEventListener'].includes(node.expression.name.text)) {
      const arg=node.arguments[0];
      if(arg)selectors.push({path,line:line(node),operation:node.expression.name.text,expression:arg.getText(ast)});
    }
    ts.forEachChild(node,visit);
  }
  visit(ast);
}
function template(source,path,offset=0) {
  const ast=parseTemplate(source);
  function visit(node) {
    if(node.type===1) {
      const attributes={},directives=[];
      for(const prop of node.props) {
        if(prop.type===6) {
          if(prop.name==='id'||prop.name==='class'||prop.name==='role'||prop.name.startsWith('aria-')||prop.name.startsWith('data-'))attributes[prop.name]=prop.value?.content||'';
        } else if(prop.type===7)directives.push({name:prop.name,argument:prop.arg?.content||'',expression:prop.exp?.content||''});
      }
      dom.push({path,line:node.loc.start.line+offset,tag:node.tag,attributes,directives});
    }
    for(const child of node.children||[])visit(child);
  }
  visit(ast);
}
function walk(directory) {
  for(const entry of readdirSync(directory,{withFileTypes:true}).sort((a,b)=>a.name<b.name?-1:a.name>b.name?1:0)) {
    const full=resolve(directory,entry.name);
    if(entry.isDirectory())walk(full);
    else if(/\.(vue|[jt]s)$/.test(entry.name)) {
      const path=relative(root,full).replaceAll('\\','/'), source=readFileSync(full,'utf8');
      files.push(path);
      if(entry.name.endsWith('.vue')) {
        const parsed=parseSfc(source,{filename:path});
        if(parsed.errors.length)throw new Error(`${path}: ${parsed.errors}`);
        for(const block of [parsed.descriptor.script,parsed.descriptor.scriptSetup])if(block)script(block.content,path,block.loc.start.line-1);
        if(parsed.descriptor.template)template(parsed.descriptor.template.content,path,parsed.descriptor.template.loc.start.line-1);
      } else script(source,path);
    }
  }
}
walk(resolve(root,'frontend/src'));
process.stdout.write(JSON.stringify({methods,dom,selectors,files}));
