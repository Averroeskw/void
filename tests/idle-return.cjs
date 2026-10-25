const assert=require('node:assert/strict');
const {shouldReturn}=require('../app/static/idle-return.js');
const quiet={hidden:false,blocked:false,hovered:false,now:30000,lastUse:0,timeout:30000};
assert.equal(shouldReturn(quiet),true);
assert.equal(shouldReturn({...quiet,now:29999}),false);
for(const key of ['hidden','blocked','hovered']) assert.equal(shouldReturn({...quiet,[key]:true}),false,key);
assert.equal(shouldReturn({...quiet,lastUse:29000}),false,'new interaction resets timer');
assert.equal(shouldReturn({...quiet,lastUse:30000,now:59999}),false,'full grace period after task');
console.log('Idle return policy: 7 checks passed');
