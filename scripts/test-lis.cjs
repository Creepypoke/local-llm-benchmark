const fs=require('fs'),vm=require('vm');
const sandbox=vm.createContext(Object.create(null),{codeGeneration:{strings:false,wasm:false}});
vm.runInContext('var module={exports:{}};var exports=module.exports;'+fs.readFileSync(process.argv[2],'utf8'),sandbox,{timeout:5000});
const fn=sandbox.module.exports;
function ref(a){const d=a.map(()=>1);for(let i=0;i<a.length;i++)for(let j=0;j<i;j++)if(a[j]<a[i])d[i]=Math.max(d[i],d[j]+1);return d.length?Math.max(...d):0;}
let seed=42;const rand=()=>{seed=(Math.imul(seed,1664525)+1013904223)>>>0;return seed;};
const cases=[[],[10,9,2,5,3,7,101,18],[7,7,7,7],[1,2,3,4,5],[5,4,3,2,1]];
for(let n=0;n<Number(process.argv[3]);n++)cases.push(Array.from({length:rand()%90},()=>rand()%61-30));
for(const a of cases){const expected=ref(a);const got=fn([...a]);if(got!==expected)throw Error(JSON.stringify({a,expected,got}));}
const scaling=[];
for(const size of JSON.parse(process.argv[4])){
 const a=Array.from({length:size},(_,i)=>i);const measurements=[];
 for(let rep=0;rep<3;rep++){const start=process.hrtime.bigint();if(fn([...a])!==size)throw Error('Scaling functional failure');measurements.push(Number(process.hrtime.bigint()-start)/1e6);}
 measurements.sort((a,b)=>a-b);scaling.push({size,median_ms:measurements[1]});
}
console.log(JSON.stringify({functional_cases:cases.length,scaling,complexity_note:'Empirical increasing-input scaling is evidence, not a proof of O(n log n); algorithm review required.'}));
