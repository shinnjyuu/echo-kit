// Echo Kit transport for the unmodified official Mapbox pixelmatch algorithm.
// PNG decoding/encoding stays in Pillow; no npm installation is needed.
const fs = require('node:fs');
const { pathToFileURL } = require('node:url');
const path = require('node:path');
(async () => {
  const input = fs.readFileSync(0);
  if (input.length < 16) throw new Error('Invalid image input');
  const width = input.readUInt32LE(0), height = input.readUInt32LE(4);
  const threshold = input.readDoubleLE(8), bytes = width * height * 4;
  if (!width || !height || width * height > 2560 * 2560 || input.length !== 16 + 2 * bytes
      || !Number.isFinite(threshold) || threshold < 0 || threshold > 1) throw new Error('Invalid image bounds');
  const { default: pixelmatch } = await import(pathToFileURL(path.join(__dirname, 'index.mjs')).href);
  const output = Buffer.alloc(bytes);
  const count = pixelmatch(input.subarray(16, 16 + bytes), input.subarray(16 + bytes), output,
    width, height, { threshold, includeAA: false });
  const header = Buffer.alloc(4);
  header.writeUInt32LE(count);
  process.stdout.write(header);
  process.stdout.write(output);
})().catch(() => { process.stderr.write('Pixel comparison failed\n'); process.exitCode = 2; });
