const fs = require('fs');
const babel = require('@babel/standalone');
const src = fs.readFileSync(process.argv[2], 'utf-8');
try {
  const out = babel.transform(src, { presets: ['env', 'react'] });
  console.log('OK lines=' + src.split('\n').length + ' bytes=' + out.code.length);
} catch (e) {
  console.error('PARSE FAIL: ' + e.message);
  process.exit(1);
}
