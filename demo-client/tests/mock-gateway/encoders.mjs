// Minimal binary encoders for the mock gateway: RGBA PNG and single-band float32 GeoTIFF.
// Test infrastructure only; never served by the real viewer image.

import zlib from "node:zlib";

const CRC_TABLE = (() => {
  const t = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    t[n] = c >>> 0;
  }
  return t;
})();

function crc32(buf) {
  let c = 0xffffffff;
  for (const b of buf) c = CRC_TABLE[(c ^ b) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

function chunk(type, data) {
  const len = Buffer.alloc(4);
  len.writeUInt32BE(data.length);
  const td = Buffer.concat([Buffer.from(type, "ascii"), data]);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(td));
  return Buffer.concat([len, td, crc]);
}

/** rgba: Uint8Array(width*height*4) */
export function encodePng(width, height, rgba) {
  const raw = Buffer.alloc((width * 4 + 1) * height);
  for (let y = 0; y < height; y++) {
    raw[y * (width * 4 + 1)] = 0;
    Buffer.from(rgba.buffer, rgba.byteOffset + y * width * 4, width * 4).copy(raw, y * (width * 4 + 1) + 1);
  }
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(width, 0);
  ihdr.writeUInt32BE(height, 4);
  ihdr[8] = 8; ihdr[9] = 6; ihdr[10] = 0; ihdr[11] = 0; ihdr[12] = 0;
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk("IHDR", ihdr),
    chunk("IDAT", zlib.deflateSync(raw, { level: 3 })),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}

/**
 * Uncompressed single-band float32 GeoTIFF, north-up.
 * geo: {originX, originY, resX, resY (positive), epsg, nodata}
 */
export function encodeGeoTiff(width, height, values, geo) {
  const projected = geo.epsg !== 4326;
  const geoKeys = projected
    ? [1, 1, 0, 3, 1024, 0, 1, 1, 1025, 0, 1, 1, 3072, 0, 1, geo.epsg]
    : [1, 1, 0, 3, 1024, 0, 1, 2, 1025, 0, 1, 1, 2048, 0, 1, 4326];
  const nodataStr = `${geo.nodata}\0`;
  const entries = [
    [256, 4, 1, width],
    [257, 4, 1, height],
    [258, 3, 1, 32],
    [259, 3, 1, 1],
    [262, 3, 1, 1],
    [273, 4, 1, "STRIP"],
    [277, 3, 1, 1],
    [278, 4, 1, height],
    [279, 4, 1, width * height * 4],
    [284, 3, 1, 1],
    [339, 3, 1, 3],
    [33550, 12, 3, [geo.resX, geo.resY, 0]],
    [33922, 12, 6, [0, 0, 0, geo.originX, geo.originY, 0]],
    [34735, 3, geoKeys.length, geoKeys],
    [42113, 2, nodataStr.length, nodataStr],
  ];
  const typeSize = { 2: 1, 3: 2, 4: 4, 12: 8 };
  const ifdOffset = 8;
  const ifdSize = 2 + entries.length * 12 + 4;
  let extraOffset = ifdOffset + ifdSize;
  const extras = [];
  const resolved = entries.map(([tag, type, count, value]) => {
    const size = typeSize[type] * count;
    if (size <= 4) return { tag, type, count, value, inline: true };
    const off = extraOffset;
    extraOffset += size + (size % 2);
    extras.push({ off, type, value });
    return { tag, type, count, value: off, inline: false };
  });
  const stripOffset = extraOffset;
  const total = stripOffset + width * height * 4;
  const buf = Buffer.alloc(total);
  buf.write("II", 0, "ascii");
  buf.writeUInt16LE(42, 2);
  buf.writeUInt32LE(ifdOffset, 4);
  buf.writeUInt16LE(resolved.length, ifdOffset);
  resolved.forEach((e, i) => {
    const p = ifdOffset + 2 + i * 12;
    buf.writeUInt16LE(e.tag, p);
    buf.writeUInt16LE(e.type, p + 2);
    buf.writeUInt32LE(e.count, p + 4);
    const v = e.value === "STRIP" ? stripOffset : e.value;
    if (!e.inline) buf.writeUInt32LE(v, p + 8);
    else if (e.type === 3) buf.writeUInt16LE(v, p + 8);
    else if (e.type === 2) buf.write(v, p + 8, "latin1");
    else buf.writeUInt32LE(v, p + 8);
  });
  buf.writeUInt32LE(0, ifdOffset + 2 + resolved.length * 12);
  for (const x of extras) {
    if (x.type === 12) x.value.forEach((d, i) => buf.writeDoubleLE(d, x.off + i * 8));
    else if (x.type === 3) x.value.forEach((d, i) => buf.writeUInt16LE(d, x.off + i * 2));
    else if (x.type === 2) buf.write(x.value, x.off, "latin1");
  }
  for (let i = 0; i < width * height; i++) buf.writeFloatLE(values[i], stripOffset + i * 4);
  return buf;
}

/** Read back what encodeGeoTiff writes (uncompressed, one strip, float32 or uint8/uint16/int16). */
export function decodeGeoTiff(buf) {
  if (buf.length < 8) throw new Error("too short");
  const le = buf.toString("ascii", 0, 2) === "II";
  if (!le && buf.toString("ascii", 0, 2) !== "MM") throw new Error("not a TIFF");
  const u16 = (o) => (le ? buf.readUInt16LE(o) : buf.readUInt16BE(o));
  const u32 = (o) => (le ? buf.readUInt32LE(o) : buf.readUInt32BE(o));
  const f64 = (o) => (le ? buf.readDoubleLE(o) : buf.readDoubleBE(o));
  if (u16(2) !== 42) throw new Error("not a classic TIFF");
  const ifd = u32(4);
  const n = u16(ifd);
  const tags = {};
  const typeSize = { 1: 1, 2: 1, 3: 2, 4: 4, 12: 8 };
  for (let i = 0; i < n; i++) {
    const p = ifd + 2 + i * 12;
    const tag = u16(p); const type = u16(p + 2); const count = u32(p + 4);
    const size = (typeSize[type] || 1) * count;
    const off = size <= 4 ? p + 8 : u32(p + 8);
    const read = (j) => (type === 3 ? u16(off + j * 2) : type === 4 ? u32(off + j * 4) : type === 12 ? f64(off + j * 8) : buf[off + j]);
    tags[tag] = type === 2 ? buf.toString("latin1", off, off + count).replace(/\0+$/, "") : Array.from({ length: count }, (_, j) => read(j));
  }
  const width = tags[256][0]; const height = tags[257][0];
  const scale = tags[33550]; const tie = tags[33922];
  const keys = tags[34735] || [];
  let epsg = null;
  for (let i = 4; i + 3 < keys.length; i += 4) if (keys[i] === 2048 || keys[i] === 3072) epsg = keys[i + 3];
  const bits = tags[258][0]; const fmt = tags[339]?.[0] ?? 1; const comp = tags[259]?.[0] ?? 1;
  let values = null;
  if (comp === 1 && tags[273]?.length === 1) {
    const start = tags[273][0];
    values = new Float64Array(width * height);
    for (let i = 0; i < width * height; i++) {
      values[i] = bits === 32 && fmt === 3 ? (le ? buf.readFloatLE(start + i * 4) : buf.readFloatBE(start + i * 4))
        : bits === 8 ? buf[start + i] : bits === 16 ? (fmt === 2 ? buf.readInt16LE(start + i * 2) : u16(start + i * 2)) : NaN;
    }
  }
  return {
    width, height, epsg,
    originX: tie ? tie[3] : null, originY: tie ? tie[4] : null,
    resX: scale ? scale[0] : null, resY: scale ? scale[1] : null,
    nodata: tags[42113] !== undefined ? Number(tags[42113]) : null,
    values,
  };
}
