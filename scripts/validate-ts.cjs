const ts = require('../node_modules/typescript');
const fs = require('fs');
const [src, dest] = process.argv.slice(2);
const code = fs.readFileSync(src, 'utf8');
const ast = ts.createSourceFile(src, code, ts.ScriptTarget.Latest, true);
const candidates = [];
let useDefault = false;
for (const statement of ast.statements) {
  const isDefault = statement.modifiers?.some(m => m.kind === ts.SyntaxKind.DefaultKeyword);
  if (ts.isFunctionDeclaration(statement) && statement.parameters.length === 1 && statement.body) {
    if (isDefault) useDefault = true;
    if (statement.name) candidates.push(statement.name.text);
  }
  if (ts.isExportAssignment(statement) && !statement.isExportEquals) useDefault = true;
  if (ts.isVariableStatement(statement)) {
    for (const declaration of statement.declarationList.declarations) {
      if (declaration.initializer && (ts.isArrowFunction(declaration.initializer) || ts.isFunctionExpression(declaration.initializer)) && declaration.initializer.parameters.length === 1 && ts.isIdentifier(declaration.name)) candidates.push(declaration.name.text);
    }
  }
}
const selected = candidates.find(n => /lis|subsequence|increasing/i.test(n)) ?? candidates[0];
if (!useDefault && !selected) throw Error('No top-level unary function or default export');
const result = ts.transpileModule(code, {compilerOptions: {target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS}, reportDiagnostics: true});
if (result.diagnostics.some(d => d.category === ts.DiagnosticCategory.Error)) throw Error('TypeScript syntax error');
fs.writeFileSync(dest, result.outputText + '\nmodule.exports=' + (useDefault ? 'module.exports.default' : selected) + ';');
