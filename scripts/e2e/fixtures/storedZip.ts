import zlib from 'node:zlib';

/** A stored (uncompressed) ZIP with UTF-8 names, enough for a dataset archive fixture. */
export function storedZip(entries:Array<[string,Buffer]>):Buffer{
 const locals:Buffer[]=[],centrals:Buffer[]=[];let offset=0;
 for(const [name,data] of entries){
  const encoded=Buffer.from(name,'utf8'),crc=zlib.crc32(data)>>>0;
  const local=Buffer.alloc(30);local.writeUInt32LE(0x04034b50,0);local.writeUInt16LE(20,4);local.writeUInt16LE(0x0800,6);
  local.writeUInt32LE(crc,14);local.writeUInt32LE(data.length,18);local.writeUInt32LE(data.length,22);local.writeUInt16LE(encoded.length,26);
  const central=Buffer.alloc(46);central.writeUInt32LE(0x02014b50,0);central.writeUInt16LE(20,4);central.writeUInt16LE(20,6);
  central.writeUInt16LE(0x0800,8);central.writeUInt32LE(crc,16);central.writeUInt32LE(data.length,20);central.writeUInt32LE(data.length,24);
  central.writeUInt16LE(encoded.length,28);central.writeUInt32LE(offset,42);
  locals.push(local,encoded,data);centrals.push(central,encoded);offset+=30+encoded.length+data.length;}
 const directory=Buffer.concat(centrals),end=Buffer.alloc(22);end.writeUInt32LE(0x06054b50,0);
 end.writeUInt16LE(entries.length,8);end.writeUInt16LE(entries.length,10);end.writeUInt32LE(directory.length,12);end.writeUInt32LE(offset,16);
 return Buffer.concat([...locals,directory,end]);}
