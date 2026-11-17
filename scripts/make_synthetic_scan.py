"""Regenerate printed scan fixtures. NOT handwriting or model-accuracy evidence."""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

root = Path(__file__).resolve().parents[1]
font_path = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
font = ImageFont.truetype(font_path, 32)
small = ImageFont.truetype(font_path, 22)
page = Image.new('RGB',(1240,1754),'white')
draw = ImageDraw.Draw(page)
draw.text((70,60),'SYNTHETIC · Printed scan fixture',font=font,fill='#172d3d')
draw.text((70,115),'Workflow testing only. Not a student or handwriting accuracy test.',font=small,fill='black')
draw.text((70,155),'Student label: SYNTHETIC-SCAN',font=small,fill='black')
answers = [('1. Multiple choice','B'),('2. Ella ___ en casa.','esta'),('3. Yo ___ español.','hablo'),
           ('4. I have a dog.','Tengo un perro.'),('5. My sister is tired today.','Mi hermana esta cansado hoy.'),
           ('6. Explain estar for cansada.','It describes a condition now.')]
for i,(question,answer) in enumerate(answers):
    y = 245 + i*220
    draw.text((70,y),question,font=small,fill='#536570')
    draw.rectangle((70,y+45,1170,y+160),outline='#84969d',width=2)
    draw.text((95,y+70),answer,font=font,fill='black')
draw.text((70,1650),'Missing accents are intentional. Preserve them during transcription.',font=small,fill='black')
page.save(root/'examples'/'synthetic_scan.png')
page.save(root/'examples'/'synthetic_scan.pdf',resolution=144)
print('Created labeled printed fixtures: synthetic_scan.png and synthetic_scan.pdf')
