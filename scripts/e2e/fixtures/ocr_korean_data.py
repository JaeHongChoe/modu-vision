"""Portable, owned hand-drawn Korean/digit glyphs; no quality acceptance claim."""
import hashlib
import json
from pathlib import Path
import sys
from PIL import Image, ImageDraw, ImageFont, ImageChops

root=Path(sys.argv[1]).resolve()/'ocr-korean-source'
root.mkdir() # Never replace another fixture.
rows=[];images=[]
def glyph(image,text,offset=0):
    draw=ImageDraw.Draw(image)
    points=[(8,8+offset),(28,8+offset),(28,30+offset)] if text[0]=='가' else [(8,8+offset),(8,30+offset),(28,30+offset)]
    draw.line(points,fill='black',width=3)
    draw.line([(38,8+offset),(38,30+offset)],fill='black',width=3)
    draw.line([(38,18+offset),(48,18+offset)],fill='black',width=3)
    draw.text((52,7+offset),text[1],font=ImageFont.load_default(size=24),fill='black')
def save(image,name):
    file=root/name;image.save(file)
    row={'image':name,'source_sha256':hashlib.sha256(file.read_bytes()).hexdigest()};images.append(row);return row
for split,count in [('train',4),('val',1),('test',1)]:
    for index,text in enumerate(['가1','나2']):
        for copy in range(count):
            image=Image.new('RGB',(96,40),'white');glyph(image,text)
            image=image.crop(ImageChops.invert(image).getbbox())
            image.putpixel((0,0),(len(rows)+1,0,0))
            row=save(image,f'{split}_{index}_{copy}.png');rows.append({**row,'text':text,'split':split})
multiline=Image.new('RGB',(96,84),'white');glyph(multiline,'가1');glyph(multiline,'나2',44)
save(multiline,'multiline.png');save(Image.new('RGB',(96,84),'white'),'blank.png')
print(json.dumps({'source':str(root),'rows':rows,'images':images},ensure_ascii=False))
