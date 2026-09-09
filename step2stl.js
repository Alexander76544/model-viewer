#!/usr/bin/env node
/**
 * STEP -> цветной STL / OBJ+MTL, консольная версия (Node.js).
 *
 * Установка:
 *   npm install occt-import-js
 *
 * Запуск:
 *   node step2stl.js input.stp                  -> input_color.stl + input.obj/.mtl
 *   node step2stl.js input.stp output_name       -> output_name_color.stl + output_name.obj/.mtl
 *
 * Если файл огромный и Node тоже не справляется по памяти, попробуйте:
 *   node --max-old-space-size=8192 step2stl.js input.stp
 * (это поднимает лимит кучи V8, но сам WASM-модуль occt всё равно ограничен
 * 32-битным адресным пространством ~4 ГБ — это потолок самой библиотеки).
 */

const fs = require('fs');
const path = require('path');

async function main(){
  const inputPath = process.argv[2];
  if (!inputPath){
    console.error('Использование: node step2stl.js input.stp [output_base_name]');
    process.exit(1);
  }
  const baseName = process.argv[3] || path.basename(inputPath).replace(/\.(stp|step)$/i, '');

  console.log('Загрузка occt-import-js...');
  const occtimportjs = require('occt-import-js');
  const occt = await occtimportjs();

  console.log('Чтение файла:', inputPath);
  const buffer = fs.readFileSync(inputPath);

  console.log('Разбор геометрии (может занять время для больших сборок)...');
  const t0 = Date.now();
  const result = occt.ReadStepFile(new Uint8Array(buffer), null);
  console.log('Разбор занял', ((Date.now()-t0)/1000).toFixed(1), 'сек.');

  if (!result || !result.success){
    console.error('Не удалось разобрать STEP-файл.');
    process.exit(1);
  }

  const parts = (result.meshes || []).map((m, i) => {
    const rawPos = m.attributes && m.attributes.position ? m.attributes.position.array : [];
    const pos = rawPos instanceof Float32Array ? rawPos : Float32Array.from(rawPos);
    const rawIdx = m.index && m.index.array ? m.index.array : null;
    const idx = rawIdx
      ? (rawIdx instanceof Uint32Array ? rawIdx : Uint32Array.from(rawIdx))
      : Uint32Array.from({length: pos.length/3}, (_, k) => k);
    const col = (m.color && m.color.length === 3) ? Array.from(m.color) : defaultColor(i);
    return {
      name: m.name && m.name.trim() ? m.name : ('Part_' + (i+1)),
      positions: pos,
      indices: idx,
      color: col
    };
  }).filter(p => p.positions.length > 0);

  if (parts.length === 0){
    console.error('В файле не найдено ни одной поверхности/сетки.');
    process.exit(1);
  }

  let totalTris = 0;
  parts.forEach(p => totalTris += p.indices.length/3);
  console.log('Деталей:', parts.length, ' Треугольников:', totalTris);

  // ---- цветной бинарный STL ----
  const stlPath = baseName + '_color.stl';
  const stlBuf = buildColoredStlBinary(parts);
  fs.writeFileSync(stlPath, Buffer.from(stlBuf));
  console.log('Записано:', stlPath, '(', (stlBuf.byteLength/1024/1024).toFixed(2), 'МБ )');

  // ---- OBJ + MTL ----
  const { obj, mtl } = buildObjMtl(parts, baseName);
  fs.writeFileSync(baseName + '.obj', obj);
  fs.writeFileSync(baseName + '.mtl', mtl);
  console.log('Записано:', baseName + '.obj', 'и', baseName + '.mtl');

  console.log('Готово.');
}

function defaultColor(i){
  const palette = [[0.75,0.75,0.78],[0.85,0.55,0.3],[0.4,0.6,0.85],[0.5,0.8,0.5],[0.85,0.4,0.5]];
  return palette[i % palette.length];
}

function buildColoredStlBinary(parts){
  let triCount = 0;
  parts.forEach(p => triCount += p.indices.length/3);

  const headerLen = 80;
  const buf = new ArrayBuffer(headerLen + 4 + triCount*50);
  const dv = new DataView(buf);
  const headerText = 'ColorSTL exported from STEP converter (node)';
  for (let i=0;i<headerText.length && i<headerLen;i++) dv.setUint8(i, headerText.charCodeAt(i));
  dv.setUint32(headerLen, triCount, true);

  let offset = headerLen + 4;
  parts.forEach(p => {
    const pos = p.positions, idx = p.indices;
    const r5 = Math.round(p.color[0]*31), g5 = Math.round(p.color[1]*31), b5 = Math.round(p.color[2]*31);
    const colorWord = 0x8000 | (b5<<10) | (g5<<5) | r5;

    for (let t=0; t<idx.length; t+=3){
      const ia=idx[t]*3, ib=idx[t+1]*3, ic=idx[t+2]*3;
      const ax=pos[ia],ay=pos[ia+1],az=pos[ia+2];
      const bx=pos[ib],by=pos[ib+1],bz=pos[ib+2];
      const cx=pos[ic],cy=pos[ic+1],cz=pos[ic+2];
      const ux=bx-ax, uy=by-ay, uz=bz-az;
      const vx=cx-ax, vy=cy-ay, vz=cz-az;
      let nx=uy*vz-uz*vy, ny=uz*vx-ux*vz, nz=ux*vy-uy*vx;
      const len = Math.hypot(nx,ny,nz) || 1;
      nx/=len; ny/=len; nz/=len;

      dv.setFloat32(offset, nx, true); dv.setFloat32(offset+4, ny, true); dv.setFloat32(offset+8, nz, true);
      dv.setFloat32(offset+12, ax, true); dv.setFloat32(offset+16, ay, true); dv.setFloat32(offset+20, az, true);
      dv.setFloat32(offset+24, bx, true); dv.setFloat32(offset+28, by, true); dv.setFloat32(offset+32, bz, true);
      dv.setFloat32(offset+36, cx, true); dv.setFloat32(offset+40, cy, true); dv.setFloat32(offset+44, cz, true);
      dv.setUint16(offset+48, colorWord, true);
      offset += 50;
    }
  });
  return buf;
}

function buildObjMtl(parts, baseName){
  let objLines = ['# exported from STEP converter (node)', 'mtllib ' + baseName + '.mtl'];
  let mtlLines = [];
  let vertexOffset = 0;

  parts.forEach((p, i) => {
    const matName = 'mat_' + i;
    mtlLines.push('newmtl ' + matName);
    mtlLines.push('Kd ' + p.color[0].toFixed(4) + ' ' + p.color[1].toFixed(4) + ' ' + p.color[2].toFixed(4));
    mtlLines.push('Ka 0 0 0');
    mtlLines.push('');

    objLines.push('o ' + p.name.replace(/\s+/g,'_'));
    for (let v=0; v<p.positions.length; v+=3){
      objLines.push('v ' + p.positions[v] + ' ' + p.positions[v+1] + ' ' + p.positions[v+2]);
    }
    objLines.push('usemtl ' + matName);
    for (let t=0; t<p.indices.length; t+=3){
      const a = p.indices[t]+1+vertexOffset;
      const b = p.indices[t+1]+1+vertexOffset;
      const c = p.indices[t+2]+1+vertexOffset;
      objLines.push('f ' + a + ' ' + b + ' ' + c);
    }
    vertexOffset += p.positions.length/3;
  });

  return { obj: objLines.join('\n'), mtl: mtlLines.join('\n') };
}

main().catch(e => {
  console.error('Ошибка:', e);
  process.exit(1);
});
