"""Horizontal text-region proposals and explicit recognition recipe contracts."""
from dataclasses import asdict, dataclass, field
import re
import unicodedata
from typing import Any
import cv2
import numpy as np


@dataclass(frozen=True)
class OCRRecipe:
    mode: str = 'crop'
    charset: str | None = None
    normalizer: str = 'none'
    text_rules: dict[str, Any] = field(default_factory=dict)
    orientation: str = 'horizontal'

    @classmethod
    def from_value(cls, value=None):
        if isinstance(value, cls): return value
        if value is None: return cls()
        if not isinstance(value, dict) or set(value)-{'mode','charset','normalizer','text_rules','orientation'}:
            raise ValueError('Invalid OCR recipe fields')
        result=cls(**value)
        if result.mode not in ('crop','detect_recognize'): raise ValueError('OCR mode must be crop or detect_recognize')
        if result.normalizer not in ('none','strip','nfkc','nfkc_strip'): raise ValueError('Unknown OCR normalizer')
        if result.orientation != 'horizontal': raise ValueError('OCR supports horizontal text only; vertical text is unsupported')
        if result.charset is not None and (not isinstance(result.charset,str) or not result.charset or len(set(result.charset))!=len(result.charset) or any(ord(c)<32 for c in result.charset)):
            raise ValueError('OCR charset must contain unique printable characters')
        rules=result.text_rules
        if not isinstance(rules,dict) or set(rules)-{'regex','min_length','max_length','allowed_values'}: raise ValueError('Invalid OCR text rules')
        for key in ('min_length','max_length'):
            if key in rules and (type(rules[key]) is not int or not 0<=rules[key]<=10000): raise ValueError('OCR length rules require integers from 0 to 10000')
        if rules.get('min_length',0)>rules.get('max_length',10000): raise ValueError('OCR minimum length exceeds maximum')
        if 'regex' in rules:
            if not isinstance(rules['regex'],str) or len(rules['regex'])>256: raise ValueError('OCR regex must contain at most 256 characters')
            try: re.compile(rules['regex'])
            except re.error as exc: raise ValueError('Invalid OCR regex') from exc
        if 'allowed_values' in rules and (not isinstance(rules['allowed_values'],list) or any(not isinstance(v,str) for v in rules['allowed_values'])): raise ValueError('OCR allowed_values must be strings')
        return result

    def to_dict(self): return asdict(self)

    def validate_alphabet(self, alphabet):
        if self.charset is not None and not set(alphabet).issubset(self.charset): raise ValueError('Training alphabet includes characters outside recipe charset')

    def normalize(self, text):
        if self.normalizer in ('nfkc','nfkc_strip'): text=unicodedata.normalize('NFKC',text)
        return text.strip() if self.normalizer in ('strip','nfkc_strip') else text

    def check_text(self, text):
        text=self.normalize(text);failed=[];rules=self.text_rules
        if 'regex' in rules and re.fullmatch(rules['regex'],text) is None: failed.append('regex')
        if len(text)<rules.get('min_length',0): failed.append('min_length')
        if len(text)>rules.get('max_length',10000): failed.append('max_length')
        if 'allowed_values' in rules and text not in rules['allowed_values']: failed.append('allowed_values')
        return {'passed':not failed,'failed_rules':failed,'normalized_text':text}


def validate_rgb(image):
    if not isinstance(image,np.ndarray) or image.ndim!=3 or image.shape[2]!=3 or image.dtype!=np.uint8 or not image.shape[0] or not image.shape[1]:
        raise ValueError('OCR array requires a nonempty uint8 RGB image')


def detect_horizontal_regions(image):
    """Classical projection proposals, suitable for horizontal text on plain backgrounds.

    Threshold rows delimit lines. Within a line, large column gaps delimit regions.
    These are foreground proposals, not a learned text detector or quality score.
    Coordinates use exclusive right/bottom bounds in the unmodified input image.
    """
    validate_rgb(image)
    gray=cv2.cvtColor(image,cv2.COLOR_RGB2GRAY)
    if int(gray.max())-int(gray.min())<8: return []
    _,mask=cv2.threshold(gray,0,255,cv2.THRESH_BINARY+cv2.THRESH_OTSU)
    border=np.concatenate((gray[0],gray[-1],gray[:,0],gray[:,-1]))
    if float(np.median(border))>=float(gray.mean()): mask=255-mask
    count,labels,stats,_=cv2.connectedComponentsWithStats(mask,8)
    clean=np.zeros_like(mask)
    for index in range(1,count):
        if stats[index,cv2.CC_STAT_AREA]>=3: clean[labels==index]=255
    occupied=np.flatnonzero(np.any(clean,axis=1))
    if not len(occupied): return []
    lines=np.split(occupied,np.where(np.diff(occupied)>1)[0]+1)
    result=[]
    for line_index,rows in enumerate(lines):
        top,bottom=int(rows[0]),int(rows[-1])+1
        columns=np.flatnonzero(np.any(clean[top:bottom],axis=0))
        gap=max(4,(bottom-top)*2)
        for columns in np.split(columns,np.where(np.diff(columns)>gap)[0]+1):
            left,right=int(columns[0]),int(columns[-1])+1
            result.append({'box':[left,top,right,bottom],'polygon':[[left,top],[right,top],[right,bottom],[left,bottom]],'line_index':line_index})
    return result
