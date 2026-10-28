// Synthetic streaming fixture: no network or business data.
const started = process.hrtime.bigint();
let sequence = 0;
const emit = (event, details = {}) => console.log(JSON.stringify({
  id: 'synthetic-1', sequence: ++sequence, event,
  elapsed_ns: String(process.hrtime.bigint() - started), ...details
}));
const syntheticSecret = 'DO_NOT_REPORT_FIXTURE_SECRET';
async function main() {
  let pending = '';
  for (const chunk of ['{"amount":', '10,"threshold":10}\n']) {
    await new Promise(resolve => setImmediate(resolve));
    emit('input', {length: chunk.length});
    pending += chunk;
    if (!pending.endsWith('\n')) continue;
    const {amount, threshold} = JSON.parse(pending);
    emit('parsed');
    const accepted = amount > threshold; // breakpoint: equality should be accepted
    emit('end', {result: accepted ? 'accepted' : 'rejected'});
  }
}
main().catch(() => { process.exitCode = 1; });
