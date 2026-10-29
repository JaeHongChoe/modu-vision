"""Actual unchanged runtime with fake process/read/file/time boundaries; no workers."""
import ast
import base64
import builtins
from contextlib import nullcontext
import hashlib
import io
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import CodeType, FunctionType, ModuleType, SimpleNamespace
import unittest
import zlib
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.ci import canary_unknown_observer as probe
from backend.engine import runtime_deadline as ACTUAL_RUNTIME

RUNTIME = (ROOT / 'backend/engine/runtime_deadline.py').read_bytes()
TEST_SOURCE = (ROOT / 'backend/tests/test_staged_update_canary.py').read_bytes()
NODE = 'backend/tests/test_staged_update_canary.py::test_actual_canary_partial_publication_never_runs_a_second_worker[after_canary_result]'


def source_code(code, name):
    for value in code.co_consts:
        if type(value) is CodeType:
            if value.co_name == name:
                return value
            found = source_code(value, name)
            if found is not None:
                return found


def selected_function():
    compiled = compile(TEST_SOURCE, '<original-canary-test>', 'exec')
    return FunctionType(source_code(compiled, 'test_actual_canary_partial_publication_never_runs_a_second_worker'), {})


SELECTED = selected_function()


LEGACY_RUNTIME_SHA = 'd98502523703705b5fef1ac06eb367a1e4e02d73d19949953e5b051edd627a44'
REGISTERED_EXIT_RUNTIME_SHA = '2a2f1dfe2c7e96811eff8a23c68abb72e8843770bf9efa431ee9097217136a7c'
PENDING_ADMISSION_RUNTIME_SHA = '4f1cc56eca4943cd9992b585da05187e380f278736cf179b49552c8f173f1452'

# Immutable de9 source is test data for metadata-only historical compatibility.
# Never exec/import/call these historical function objects or replace ACTUAL_RUNTIME.
REGISTERED_EXIT_SOURCE_COMMIT = 'de9e8a25587e297eba7577d32ca4fd73f6d36b97'
REGISTERED_EXIT_SOURCE_BLOB = '8ab55a298088b97ed9dabf415aeccab34414fdcc'
REGISTERED_EXIT_SOURCE_BYTES = 22218
REGISTERED_EXIT_COMPRESSED_SHA = 'e6493e6861aad266ba83a776c9e592c8b4f6fd8772c5337f45f3df178f3869c3'
_REGISTERED_EXIT_SOURCE_B85 = (
    'c-p-@YjfN-lHcc7aGeh%y%Z%on_O<ZlRHHkdv#GXF3DMESEXW6Bu3(jLox)Z$L9Rsue<ReNP?rWcXKMsnjwHjqhIK5H2C7hi'
    '`7Hn#7@fMG|vj=#6{|?S&=Y@xAlE?pA|PwdCggU&r+wV%Y<>hcT<;b<vf)2T{xSqRI7Mev!pDNEYIR5D+?#%PEj^aQf{i8HO'
    'wh%hw&t?3}{Ww7=9_&;q1kW7qfL;Zk#Auw@q8KC~~q*Rn`q)27m}C7hSI7<c<|-$ch`lJS^)B{>FJ!J`^mCem;p}#3PUEr>J'
    'JXwv0Cllw44xhx9s2yn^k$cojbsvzda()hC|aKwtf>y_VqAljgPtS_4`2gKWdp=LZ(w)od-+2Xt)MW0Pms3UB#j6BqFft3|K'
    'osX`vf9*4!#Y&MIQzbub_{&2E7yKoi?X!sHS{>Yw=vjm9<Yw&!tXLPzc`VqQo1jExZxr08lS(3+`JJEZwl8?Z5Sbf6gGY5Wv'
    'Wj``j0SB}D7&t6xy`RL%Epygc&bZSSO_l>>;6E}4`tUYyP&i;o>#W|eG(^@Typ*kh`&p4SQRH!!uLA(h*4bm=q!~z8Q`S$um'
    '>Pc+71^f3x%6`)P8Bz|2L0Z{SKR?Yhu8tuj18Uw5pM2M{OpNo7jkx(e9<A`hp>h+zh^+85yUXL-IS?!Q2xik!Js+HQU=6mo?'
    'zClf6hU&hmfLm3Y|(GmK7^J-8q0KtLXfAb$0r<|Gi-_u&KS02kt65J~>|=eOR6U%?%FxLEG8t?dtS&^%0wVCtD-nBHpmY!mU'
    'c4J-QCdIdjg+f(_6B-0Fs<3gKuCyy3U>;B5?JW*yr$aRW@}gzT6{xJdo1sh_$I_@mTp0f?=M9*PQtt|p1JD2S*)xlh+ONn6+'
    'OaWPF@{HYmOmx53VFQStRAe|>ihP;V@eat6g=qbGLamwxk^Ltj5{$gPc#2&_1?4ajmj|r<9XLYfx>$09p6b3Yu)E<#=O{*4b'
    'Rb=+(b({eO&QZTMjKF&Cid5`DX{n<nF1d}18)&*_>#_#oJ+h=lQ{eh`jxRpct$~^1yHv`uZNRFt-wDTf-LTq<qMRk;Spj&$P'
    'UT5VKtq$pmt-}&%k~fy!gG=1Eq8%!uG*ARmpBcURKpT|1*imM!9|_nPF6I1q3vsgAed%%J~cPMaXc5v0nKjUCb*0|2Sofnc('
    'pArdSKL3*WZSVK_E3iP>2?*9tns!d}DRQ9>M5pi|o>paY>dDz?}_fL1T97L?y{f&W0j9()h2Ok6CjIO9KZ4NF8S=6WYW&APH'
    '&C!0MO8@VJG(b@or(VK(J`oP)&}fP1Jj*nnd4LGBrV*u?c6h!JdQWqWfAEnw<+cAY;#mzJXsg7#ktUpN4F2HwU!10;*Ie1NS'
    'As8o=LvjJ2&(7TFjpa4K?i(lIq*qy;X)&nvyTxhh00xIS@EC7fmz1$%8g~dQWDk1l0OoY%mbl?L^2k^kps#%qTZkZ%S+sS}3'
    '&brL=@&VeQANXI$M<?dm29FkD)$8#(^m5gnfr8VGRkGO^17nBy2QQ0J8?3Tri{D%h^KRk@(C26o=C1;!P;*ytoIE{PZVFnZj'
    'YkCAAnfOj!pfbav<9Pz@CP8p9HG0yjg;P7Bp4DIJVL#d21NHlR~8W@NpR<6+9&1#PL@E+EQzFCMHQ>TE^qX}Pose&4bsj|x='
    '?<CtBvX&FbD;^EJCEKD;$T9=j1rh=cDuG;Ro??`ODGi7LOq~D8D^8KpzMsd7(FED>4tD)aYHTQ$lE+2JQnG2^6oknIkG;+Jb'
    'vUd=224{|5dG*LlltJ@|~<|5K58^bq{9qV)XnOoPp|LJt+>{yi`Xy>@aoyZr^_PzkrAwd#%32B|5~3)(W?phGQ8M_&=p-I$b'
    'Jehb2uPqv!A&rq>wWLR%de8Tv?%u*ilrwv%!JCt3Bb5J#rra52-Ob|O@flqi_;RGDmVbe%q4URy>V0;0l3M8V=z$Gu&L?Uf>'
    'HFdPL2%Y270t=@m;{&^P!ySm*8s{1fD!Ri+xmZ}$_yz#a$r=-NH!!mW+%;<+7%NN<fF*Zw#{uOsjNdiGJrJa*=HX}P22c(iT'
    '}dxV!Opp~k0N#hsh&`=$bUo_Lj?$g9aYTY3sPKv5@NRQdz>_b_BZD}L9k9Z4Vq*VaBU03WXHkJDUsOfC<j0e+x1feqJzANi>'
    'FRxk{Fy9@YWDW$C)*@<pZuvc=BT=0C82vj@-TIL<bH$6#fRTa5nqzL6nUCNZ*!gv*5i9y5LR-{Q{@(b!3a#5ezg`aI5>h!R?'
    'yWs61q5z+1u9INGdhcY#6>72N8n7o%v}8=@lEg>wCrvE1;MXC`&^Q%M9Q0mWh^nYcKQDvna+$whtTykiBcMRtZglI>6~mfSo'
    'YF(PzMph+7<@+R{<RvIaxq5T1~!kfAT$Gt`mD<ii_Dm2)dH_)157%nm@0s;WdbCGHSiPFGS&Pk@2q<?r*$Av&bT8VvhRh2gc'
    'MuVdL*x`UzI2)M*X@kYGW`hvWcq-N5gt`lr81^O!loTc$qYc|!gG=Jkw@z}-!Cl@Xi~D|caq^2?P=%TWOAh4D6FZb9No7h+B'
    '#*STY|UYSCQ;hL-AB3JM;+Nm<Kp^Z2D<@loStFZYV=?i&TpMlfp!r*TW01}&_=SN-JpCh&j61=!R1H8>H8y%CNR$T3+LN!zW'
    'Mswxnqm?q`3EF0O`$APW#Ap*%s*_qLKS!w<DodgVm)h$u=>HG^NC{qU~D+>t0aD4?qA<1KW2cwJ_Zkp~dk0%lUV&{`2+K*ch'
    'P#BK-i!CH#gps9);2zU%+-_53R2Rh|tq44O8LCx&0XzVe;LLP&yr*Fi2_;)wo|{VQZ0;mh2)gm!QhI4%d0k~3^f^YxYAcO9i'
    'zdv8G|&dTO3Qku9DIFc);WqH?D^vp(6Df@`sOnj&G-NHz3hILzbm+t;`R_s?CeB-_Nv4lTWmM((o2LDI&7cO9(#1*A>L9?s2'
    'S@fN885J+WZvx>8h;|Umdx!zvAAb0Yi`yJ1ASJ`x)5EiO;H@9IpM0ej;4*pwMc7(XbTFOoo!2j49lZV%{r9g6XkVL9lPGx(+'
    'S8jCu*Bqn=f*tAGB=<Nk}rUfH3Z@ydL%bEl$*K=Gz%Q0SwP+bgPADs9!i;%F6=oaSf~k|Q~}_=DAAvwlOiB2!~i@?J=NVVN@'
    'KAZNR25mI)6+_pLZRSVCD`5S;4i)OYkBna<5a|T<;3lv<<H?E`<Lo99G)VYkr$m+w;aElMfMc+U91eN+btWGkp_%fI5Rw{V~'
    'E$%T%bO8+;w7O+`w@+9Y?3zB#6r&fmfrMj2s66>Fm*x6wKVRqqnCy?!clV+6*#blnCrlKCTW&%x_TAxe(#p?iEy^z0AlGS4W'
    'oGWR@x-h30`kyVQ1LRL}uJ!OMXU<0QUV)MDpHcMtMI;fiaIA(6JJQ#AkkI&5Nj&EU1FH>l7#djS_Nk%EUD#2t+Ey*xZAJfUh'
    'ty(9RKoU4?wTvgMNWt4u{%bhSF+Pt~4=0_i$GAb07Xl~Gx1((3e-&d+9VQZhLurDx^SG~xo`4Z1l<<ns_In^NO<RGlq}n!=<'
    'ODCrx3Nb~E_sCUQkV3UN-%K-@+FF5S}B8KI%slwM+jRrjEHYoFhzJf2(eKi$)r36-5E?@*u)Wx?q>85MDnN8zY3s6s3$0%q2'
    'GNv;BNPag)!FZN{M`lGz8NCr2hI7gfQ_OOY~Su<<XDYAO^BJLc?a1Oqt!zCG~Ti)!+#^Qm9~VbpT}*bls>W-G+rHfJ&Ghy#!'
    ')*@AWI^6dZs@XA|Q#Ef4J-++dzGa%o%R_C`m+GJP-sE4j_`)TIElaUS4{$TtyQ`;u+@GHjw)k`ynTfcD`QXt|CPKndbiN`Rp'
    '^(Ud)ZkCI|>1NIUqR}GTt9WW8m5rP;b%UF(rR4!vUO4v~k@LX$9T}*%3k=wyCc-s`ZlNBYGlSv{Rb6u9XXAHc2btR63h_Yfg'
    '>>3ZQbPivlwiS3!!n@-$CCK*l)ueFChW>^p46(3K1I5s$Sa?_wxwq06RGwu2ywYOPG)z~zTkf<8+pg|v%1exb+^bP+YgJR!v'
    '~D2r#J#davr{+{TG|rqr1FT09PtM=q(ebA0siyZl*T8q$R)v6aCs%qZX49Nn%!g^zM<dIrJZhLZp{Q!dN+3dS#GW~77Crsga'
    'r_(6n2kzb?K8lyJr?i3i-hFXF4@R)f8IVWst&23ghf}L@bjLic<L79rWWdl;>;Zk%DmX;qb%H7t!BWKmBmB6g#DzVV1eR1U8'
    'bx9cqd>=&ZbGlUpgD^Wn7HMoN!oBz9Wmq(eD$dl02adtioT=?)UN<ftZHgdH4432l(2>%dvJJxU$GK+G7tIygA6rwT;%g6T-'
    '_md{d&809TFf5js(`PrNzFf%I``lC$H*xlS6V}%J<hUD2`)<hZs*;7$$q;m-Ff4OEN3W0s1lt_j7ke%G1kT%Mi(l#LX9MGVw'
    'k3IQd5z8G3Kv-c~1(rg|8dwqLH8;KVixdTDyp(Ia3hCsSV>;S%|L#uZ9bpuJ*P3dEl2d1Yrhcf6)_c|?*kP;Px=q8{WJQW(T'
    'W4OhLp<%PNaYXXH1(`r>=bc^i`^!R-Z)sHq6XrM#QJIntS8R}g+-Xyx~<EiRdnThztOfnCxMb1^;`fQI^KE@)$M8{{xq99Cb'
    'U^fBFxA+z{GpsWt;c%OqYNmX8qK_sZ&6UUQvL%gVdtb%Oh!X=J^itvONkMuzL`-JqQ1poSHTVnyCZ)?Y2SXWR0HE)6_oZ%yQ'
    '@}oOjxy=L5{_lo94yk5pUkbBk7$js3Nw%VVYx)%UT{?6!1O%01fDzj6-oB<nsaTTaza0;bd`;hG6=j!U<T&W@cl;}CtZ7(>8'
    'tw8=k@8aC-_A}wRq@IgM*y_I|7k8S5TvC@%1Ih;cZNbp#cR93hNsv7yOT3nqrlVH2k6}R2c&ROMdziP-8K;}k1vfFV@jTj8G'
    '*Y(YxS^fY>=ff*6%AI2sh1XLRs^$S*@dH;&UDuzNx~R1iN`Eks6af1u*$Cjrav@hVZm>~NP-9r6<@QRr>|Mr1W|l%S*P36K7'
    '+Tu`cbsRp^cM}E)Zw!@2Z?l%n+IA~G(mSTBBeD$A;zF*5Z=yosn+lg0QOh|yYUjp!fYj%fq)5XBMLO}R8;l%C{5T6AJuAW&j'
    'h;3;a0Dn5EC6jVO*oUOzuWbn(7iKqUL~$#m;rZuWc-92M|w98(kxzu6UqB@9E}1K#>#_&aY7T?$}8i6ti;RhnTaasxIr09gf'
    '=OqfRll<7;PxFf`G}Ye%Ap82!Vg8mrIQE<l#!9P377MRy8dX^cpPVbCJUi1j=w#QwEqEnq2b<A^&ylP7MYbaz*RiQKbYE*TY'
    '`OHM!ESK?|rVC!?ev*^RpAidGG1=^$G8b}Cj3+aoY(an(5uCi`1LTVIhsIFUc%hLB!ox^T>_P2$p(y;04S)zCuGn{hxFiM?X'
    ')0Xt*nBvoY?VKYux)<5Bj`d4?GN`QB5xK!+1bYl;Z2gQUmo(_gxI_iNLQI}dK!~cX@wC>uxX&cIE~7K(t_}QiRNigv;22cW_'
    'K4JNSKUumV~e(IG1xuatfzllGs5QZu<y+e6UM^CgIkiNh>g0wAj*!r&5^#2qffdFBD$)^Ok@u6<16RS3po-#eE0324)DkH4@'
    'n)n^p5AA6lzmcm_!@yY+8<Wt$0oX;29RsQPI~cJeg2%Rndu998Ie>FntX}ZehB1L2nmkK_liTo;iQb*4@w%Rq#u<mkp!S!F4'
    'X(uNSFI9ez!oWZICK8_=i98HVT4<_SvCxWhy8_gJegZ7c5`PwD#~%8*Le0%9Pll*^I<s3zujcX%MR|G~+rLS}q>BUTqs-k%('
    '!P#La&KNGD8h5!v=A6KH2mr{z3)s0E$K$R6<oV@$-<n+|vwW6mN{qG<<6Zon}4{_Fbs=Vu)o=i)HwUzEhbg8EXzdSBDo+)~W'
    'R<ureS8MqL=2{)ygZ!B{#6+Yx#^edzaFKP&AUxew7iabM*gOUxn1@}#Z(HRw8YA)R3|%O^26hX4r@K|UM3s&|WTcIixbl>fz'
    '=?TX5Fu9YQN&0Lud}Kt>(KcLqz-IUti%Kec26cDPDGr6#Zh<rfM}>ztkjvzlqQJM5jw&_L@=G*wSMos+Ge3X5}^$;$}k7qLJ'
    '5&fe2i5PyrHt_ZWXSszM~b$IlJk^X6Oym6rz3(^;HeIJh++-RQ3<up(kEf!X?wC(4JWaAb)ng{?he{6WyXL_V9K<X<69d8b@'
    '!(mlrCjCP`dfHH+<)C{Z)WlWy#n{NM!{c=A~*=+H!J7#U*M7yAeR9HUF7A}iLViLu0T>bB|#yMH%^gXC%+Z?4nW;d961>Rzq'
    'G<&|3b2`^2^k1d%w{6yCu<Yx}rDnBM@c%p*$swZ{A&J!H$9lm-<Az!DQ)U}QC<vVK8w>D;$$`jT5r`7SY-y3J3yHpO9UyByN'
    '9-oVP75L&=fvVoLLhS#_e2Qx&;QqhDBkhlnxy!qY52A{rW;bmf*L17jh#I7F%f5j>v%0zZo`w=Nx?@Q}COV(d&u797BHN0|K'
    'G>1>a3+l)US-2=VyE{v(C#7PHt8Ly7Eh+*1LtL6dSjx<m@;5a@{o#8!r6_;5f)FBrCNBt@9cf=;HBD2-qZoUec`Ixl-d-y>0'
    'o$Fp|?09q@*@mw|H;8O~{|XG_-!jMt1|E7iy2)SOafCZ*^%K1wGu9AhczXB~p}ZFU(LGuZ)-SR);cjB9~}10HZ1x9OKRGTEv'
    '*>2f;2?lo~8v3Tv3W$?=xn`#^Wu@R9=+!d&AvMq4Dlas)U_DP1Z8cdD!cm#TA;MIAL*46Y!?`ogWQ8UCM()fvfRAZ`fP*BMA'
    'P-uner%VF2NxslV!dhFTM8zXe_@A%q`ktmyXMbJEI$qh8@@B_oi0HwDSr(-vP%Cmh~U7HDayojWVPvIZ!S8vdh8N~O(O~(^B'
    'U>@$+=)2+w>BWY|E}--ukFrQJaVhTH5e4_ITAVxYmi9>$ohuckoSt!BFL7BIr`P0IYmF0&Uq~8=WiG^@0F@7Z15`cu4PL&~D'
    '8lEFP?}XNJG`nzheo}*Re@VDQm0-FgM!gjuWL}UtX%ik_xKn6I0p44#p@2k=fkvtD0ADz&+boW8mUp#W;Wv0pmEr&ihvY6+*'
    'eEkV+oH3MrKc1AO9s*O?<yF$thm|VaUV>5d<tBEA(!?tvt|q(WzXmxjf&JN$5`h!h_e};5r8@>$1}jNP5^Q*u;<U;o#tviPJ'
    'tVZEt$%mO@Hour>VY&OjzlW2eY9?}cU0_U;FXS`bI|=j0Sl<`XR0=c%+ed5ZnJbK{(V^=856q{*k#<)%DL;cb792w@%Qg-nz'
    '{h^+fI3Jh}#M@^mo@d%YFh8b_MRujV9x^5{|fpQ`IE;?CfxT|J*AG~I_V7SBIhgH8jhs*?yWyt&s*=J3+X6qeo#Fx%$rJ$qk'
    'OrH<#CCS?k#@h&V#zAwR{Ml*QGIR9gntYZ81bUbNzGT(XYvPREc?bR*v1O{S-7YDv@yX7ZxcBHDsIRZTwat3(yYBj%d?A1lK'
    '*$z3!54uTE_ayUQ=Fi)u3MI4Dn?#vm)>NW#WzLCn=Ij>b0ktljq=R~;MD!hOkRhV-Z8xmfy+&Yn66VlFn<M3S%x{JVY%tl+f'
    'SZ-hZ$w$JG2*D-(%+9XXrugnPd|8j`!Tb-er=)`Yto`#R`E;p%;#auQ(zpXdW9k5Q5<c^b{FOPX!C{X=fV5`$_bxL3k(Jp{~'
    'I211txK?${F-#eu$WyCu}zE)4AV5y3Zf1)rREanD!0d4yrT9Bjc0DO~=W-n-kr9j>>l`A~12(XRk-1M^)`_+}cJ95<hiYxTy'
    'jhH)us#?k|fDq)KA<zG*h9|`_H7`?bJ(r>6)eLPzpNAJ&9N6U+g=-v71=l2jqpD&MAXGbTe%VXC9zb>;V0f7hD2Z0MAGNZGU'
    'sQ)~v%L*2Fd1byuhc`?<9D@~XcjtoTfn>NlmlugZQg%O$w15|dc1B9uWqDk2tVjWjYE)qF8J{%x+P47Kat5f|M9!7DipY>Dy'
    'f#~V#sj}su&jNo`7_efz4fc_e2c1}?(JZoBN9i4XGhCZ<h>2%$=TcG`4W0W$IHXx)049$zV~OjJpO-`75)0M`ERb_Ub}NM74'
    'vUm@H0|myv1zGJE5$lJ-YC#_i7PwHZ-XA0z#=$uU=<S)b-4q6D3?*s<)mmW%~4KwnZ%UTk-lvZ71`!-46V2n*C|^e+)%h`T'
)


def registered_exit_runtime_source(packed=None):
    """Bounded immutable-source decode; compile metadata only, never execution."""
    packed = base64.b85decode(_REGISTERED_EXIT_SOURCE_B85) if packed is None else packed
    if type(packed) is not bytes or len(packed) > 16384 or hashlib.sha256(packed).hexdigest() != REGISTERED_EXIT_COMPRESSED_SHA:
        raise AssertionError('Historical compressed source is not the immutable fixture')
    decoder = zlib.decompressobj()
    source = decoder.decompress(packed, REGISTERED_EXIT_SOURCE_BYTES + 1)
    if (len(source) != REGISTERED_EXIT_SOURCE_BYTES or not decoder.eof
            or decoder.unused_data or decoder.unconsumed_tail
            or hashlib.sha256(source).hexdigest() != REGISTERED_EXIT_RUNTIME_SHA):
        raise AssertionError('Historical source failed its exact size/hash bound')
    return source


def runtime_metadata(source):
    """Compile and construct function metadata without entering historical bodies."""
    compiled = compile(source, '<historical-runtime-metadata-only>', 'exec')
    owned = source_code(compiled, '_OwnedGroup')
    metadata = SimpleNamespace(**{name: FunctionType(source_code(owned, name), {})
        for name in ('__init__', 'leader_matches', 'member_state', 'observe')})
    return SimpleNamespace(_OwnedGroup=metadata, **{name: FunctionType(source_code(compiled, name), {})
        for name in ('_terminate_owned', '_observe_owned', 'execute_owned_process')})


def legacy_runtime_metadata():
    """Reconstruct exact old bytes and compile metadata; never execute/import it."""
    current = b"""            if current.create_time() != identity['birth']:
                return 'unknown'
            command = tuple(current.cmdline())
            if command != tuple(identity['command']):
                # Linux can expose [] after this registered generation has
                # become a zombie. This is exit-only evidence, never live
                # identity, descendant enrollment or signal authority.
                return 'exited' if not command and current.status() == self.module.STATUS_ZOMBIE else 'unknown'
"""
    original = b"""            if current.create_time() != identity['birth'] or tuple(current.cmdline()) != tuple(identity['command']):
                return 'unknown'
"""
    registered_source = registered_exit_runtime_source()
    if registered_source.count(current) != 1:
        raise AssertionError('The exact reviewed source delta is unavailable')
    source = registered_source.replace(current, original, 1)
    if hashlib.sha256(source).hexdigest() != LEGACY_RUNTIME_SHA:
        raise AssertionError('Reconstructed legacy bytes are not the immutable original')
    compiled = compile(source, '<legacy-runtime-metadata-only>', 'exec')
    owned = source_code(compiled, '_OwnedGroup')
    metadata = SimpleNamespace(**{name: FunctionType(source_code(owned, name), {})
        for name in ('__init__', 'leader_matches', 'member_state', 'observe')})
    module = SimpleNamespace(_OwnedGroup=metadata, **{name: FunctionType(source_code(compiled, name), {})
        for name in ('_terminate_owned', '_observe_owned', 'execute_owned_process')})
    return source, module


class ReaderError(Exception):
    pass


class Missing(ReaderError):
    pass


class FakePath:
    def __init__(self, value='private-path'):
        self.value = value

    def __truediv__(self, name):
        return FakePath('private-path')

    def chmod(self, mode):
        pass

    def write_text(self, value, encoding=None):
        return len(value)

    def __str__(self):
        return '/SECRET/private-path'


class FakeFile(io.BytesIO):
    pass


class World:
    """Boundary doubles only; all ownership/control-flow code remains original."""
    def __init__(self, mode='leader_mismatch'):
        self.mode, self.calls, self.commands = mode, [], {}
        self.owner = self.original_unknown = None
        self.marker = KeyboardInterrupt('SECRET original interrupt')
        world = self

        class Process:
            def __init__(self):
                self.pid, self.returncode = 101, None
            def poll(self):
                world.calls.append('poll')
                if mode == 'final' and world.calls.count('poll') == 2:
                    self.returncode = 0
                return self.returncode
            def wait(self, timeout=None):
                world.calls.append(('wait', timeout))
                if mode == 'empty_then_changed' and sum(type(value) is tuple and value[0] == 'wait' for value in world.calls) == 1:
                    raise __import__('subprocess').TimeoutExpired('controlled fake command', timeout)
                self.returncode = 0
                return 0
            def kill(self):
                world.calls.append('kill')
                self.returncode = -9

        self.process = Process()

        class Reader:
            def __init__(self, pid):
                self.pid = pid
                world.calls.append(('Process', pid))
                caller = sys._getframe(1)
                if caller.f_code is ACTUAL_RUNTIME._OwnedGroup.__init__.__code__:
                    world.owner = caller.f_locals['self']
                    world.original_unknown = world.owner.unknown
                del caller
            def create_time(self):
                world.calls.append(('birth', self.pid))
                if mode == 'changed_birth' and self.pid == 101 and world.calls.count(('birth', 101)) > 1:
                    return 2.0
                if mode == 'registered_mismatch' and self.pid == 202:
                    return 3.0 if world.calls.count(('birth', 202)) > 1 else 2.0
                return 1.0 if self.pid == 101 else 2.0
            def cmdline(self):
                world.calls.append(('cmdline', self.pid))
                count = world.commands.get(self.pid, 0)
                world.commands[self.pid] = count + 1
                if self.pid == 101:
                    if mode == 'initial_error' and count == 0:
                        raise ReaderError('SECRET /private/read-error')
                    if mode in ('initial_empty', 'pending_empty', 'pending_origin', 'pending_admitted') and count == 0:
                        return []
                    if mode == 'pending_empty':
                        return []
                    if mode == 'pending_admitted':
                        return list(world.process.args)
                    if mode in ('exit_empty', 'empty_then_changed') and count == 1:
                        return []
                    if mode in ('leader_mismatch', 'unregistered', 'termination', 'initial_empty', 'duplicate', 'two_unknown', 'zombie') and count > 0 or mode in ('empty_then_changed', 'enrollment_changed') and count > 1:
                        return ['SECRET DIFFERENT /private/command']
                return ['SECRET /private/original-command']
            def ppid(self):
                world.calls.append(('ppid', self.pid))
                return 50
            def status(self):
                world.calls.append(('status', self.pid))
                if mode == 'registered_error':
                    raise ReaderError('SECRET status')
                return 'running'

        readers = SimpleNamespace(Process=Reader, Error=ReaderError, NoSuchProcess=Missing, STATUS_ZOMBIE='zombie', STATUS_DEAD='dead')

        def group(identifier):
            self.calls.append(('group', identifier))
            if mode == 'interrupt' and self.calls.count(('group', identifier)) == 1:
                raise self.marker
            count = self.calls.count(('group', identifier))
            if count > 1 and not (mode in ('empty_then_changed', 'enrollment_changed') and count == 2):
                return {}
            if mode == 'duplicate':
                return {202: {'state': 'R', 'session': 999}}
            if mode == 'two_unknown':
                return {202: {'state': 'R', 'session': 101}, 303: {'state': 'R', 'session': 999}}
            if mode == 'zombie':
                return {101: {'state': 'Z', 'session': 101}}
            if mode == 'enrollment_changed':
                return {202: {'state': 'R', 'session': 101}}
            if mode == 'unregistered':
                return {202: {'state': 'R', 'session': 101}}
            if mode == 'foreign_session':
                return {202: {'state': 'R', 'session': 999}}
            if mode in ('registered_mismatch', 'registered_error', 'registered_live'):
                return {202: {'state': 'R', 'session': 101}}
            return {101: {'state': 'R', 'session': 101}}

        def getpgid(pid):
            self.calls.append(('getpgid', pid))
            if mode == 'constructor_interrupt': raise self.marker
            return 102 if mode == 'changed_group' and self.calls.count(('getpgid', pid)) > 1 else 101
        def getsid(pid):
            self.calls.append(('getsid', pid))
            return 102 if mode == 'changed_session' and self.calls.count(('getsid', pid)) > 1 else 101
        def killpg(pid, signal):
            self.calls.append(('killpg', pid, signal))
        def monotonic():
            self.calls.append('monotonic')
            return 0 if self.calls.count('monotonic') == 1 else (31.0 if mode == 'termination' else .001)
        def popen(*args, **kwargs):
            self.calls.append('Popen')
            self.process.args = 'unsupported-origin' if mode == 'pending_origin' else args[0]
            return self.process

        namespace = {
            'os': SimpleNamespace(name='posix', getpid=lambda: 50, getpgid=getpgid, getsid=getsid, killpg=killpg),
            'signal': SimpleNamespace(SIGKILL=9),
            'subprocess': SimpleNamespace(Popen=popen, TimeoutExpired=__import__('subprocess').TimeoutExpired, SubprocessError=__import__('subprocess').SubprocessError),
            'time': SimpleNamespace(monotonic=monotonic),
            '_optional_psutil': lambda: readers,
            '_group_members': group,
        }
        self.module, self.boundaries = ACTUAL_RUNTIME, namespace
        fake_temp = ModuleType('tempfile')
        fake_temp.TemporaryFile = FakeFile
        fake_temp.mkdtemp = lambda **kwargs: 'SECRET-private-path'
        fake_path = ModuleType('pathlib'); fake_path.Path = FakePath
        self.modules = {'tempfile': fake_temp, 'pathlib': fake_path}

    def execute(self, **kwargs):
        with patch.dict(sys.modules, self.modules), patch.dict(ACTUAL_RUNTIME.__dict__, self.boundaries):
            return self.module.execute_owned_process(['SECRET /private/worker'], deadline_ms=30000, **kwargs)


class ObserverTests(unittest.TestCase):
    def make(self, world, **changes):
        factory = getattr(probe, 'Observer', None)
        self.assertTrue(callable(factory), 'source-pinned Observer has not been implemented')
        values = dict(module=world.module, runtime_source=RUNTIME, test_source=TEST_SOURCE,
                      selected_callable=SELECTED, canonical_test_callable=SELECTED,
                      selected_code_anchor=SELECTED.__code__, nodeid=NODE, point='after_canary_result')
        values.update(changes)
        return factory(**values)

    def run_pair(self, mode):
        baseline = World(mode); expected = baseline.execute()
        current = World(mode); observer = self.make(current)
        old = sys.gettrace()
        with observer:
            result = current.execute()
        self.assertIs(sys.gettrace(), old)
        self.assertEqual(current.calls, baseline.calls, 'observer added or reordered an original boundary call')
        self.assertEqual(result, expected)
        return current, observer, result

    def test_current_exact_source_captures_its_own_cohort_without_legacy_credit(self):
        self.assertEqual(hashlib.sha256(RUNTIME).hexdigest(), PENDING_ADMISSION_RUNTIME_SHA)
        world, observer, result = self.run_pair('leader_mismatch')
        record = observer.public_record()
        self.assertEqual(record['instrumentation_status'], 'captured')
        self.assertEqual(record['runtime_source_sha256'], PENDING_ADMISSION_RUNTIME_SHA)
        self.assertNotEqual(record['runtime_source_sha256'], probe.RUNTIME_SHA256)
        self.assertEqual(record['first_unknown']['reason'], 'leader_identity_unproven')
        self.assertEqual(record['first_unknown']['phase'], 'wait_observation')
        self.assertTrue(record['first_unknown']['row_session_equal'])
        self.assertEqual(result['status'], 'uncertain')
        self.assertEqual(world.calls.count('Popen'), 1)
        self.assertEqual(observer._sites, {
            ('observe', 288): 'leader_identity_unproven',
            ('observe', 296): 'unregistered_without_live_leader',
            ('observe', 297): 'foreign_session_row',
            ('observe', 302): 'registered_identity_unproven',
            ('_terminate_owned', 382): 'termination_leader_unproven',
        })

    def test_registered_exit_cohort_remains_bound_without_historical_execution(self):
        source = registered_exit_runtime_source()
        canonical = ACTUAL_RUNTIME.execute_owned_process
        observer = self.make(World(), module=runtime_metadata(source), runtime_source=source)
        self.assertEqual(observer.public_record()['instrumentation_status'], 'ready')
        self.assertEqual(observer.public_record()['runtime_source_sha256'], REGISTERED_EXIT_RUNTIME_SHA)
        self.assertEqual(observer._sites, {
            ('observe', 221): 'leader_identity_unproven',
            ('observe', 229): 'unregistered_without_live_leader',
            ('observe', 230): 'foreign_session_row',
            ('observe', 235): 'registered_identity_unproven',
            ('_terminate_owned', 315): 'termination_leader_unproven',
        })
        self.assertEqual(observer._next_lines, {221: 222, 229: 230, 230: 216, 235: 232, 315: 316})
        self.assertEqual(observer._phases, {388: 'wait_observation', 407: 'interrupted_cleanup_observation', 410: 'final_observation'})
        self.assertEqual(observer._observe_leader_line, 205)
        self.assertEqual(observer._member_returns, {189: 'reader_unavailable', 193: 'birth_or_command_mismatch', 199: 'birth_or_command_mismatch', 202: 'reader_error'})
        self.assertIs(ACTUAL_RUNTIME.execute_owned_process, canonical)
        self.assertIsNone(sys.gettrace())

    def test_registered_exit_bytes_cannot_authorize_current_functions(self):
        observer = self.make(World(), runtime_source=registered_exit_runtime_source())
        with observer:
            self.assertIsNone(sys.gettrace())
        self.assertEqual(observer.public_record()['instrumentation_status'], 'code_pin_mismatch')
        self.assertIsNone(observer.public_record()['first_unknown'])

    def test_historical_fixture_rejects_tampered_compressed_bytes(self):
        packed = base64.b85decode(_REGISTERED_EXIT_SOURCE_B85)
        tampered = packed[:-1] + bytes([packed[-1] ^ 1])
        with self.assertRaisesRegex(AssertionError, 'immutable fixture'):
            registered_exit_runtime_source(tampered)

    def test_historical_fixture_rejects_oversize_before_unbounded_decode(self):
        packed = zlib.compress(b'x' * (REGISTERED_EXIT_SOURCE_BYTES + 2048))
        with patch(__name__ + '.REGISTERED_EXIT_COMPRESSED_SHA', hashlib.sha256(packed).hexdigest()):
            with self.assertRaisesRegex(AssertionError, 'size/hash bound'):
                registered_exit_runtime_source(packed)

    def test_pending_empty_refusal_does_not_invent_an_unknown_add_or_signal_authority(self):
        world, observer, result = self.run_pair('pending_empty')
        record = observer.public_record()
        self.assertEqual(record['instrumentation_status'], 'captured')
        self.assertEqual(record['initial_command'], 'empty')
        self.assertTrue(record['ownership_refusal_observed'])
        self.assertEqual(record['unknown_count'], 0)
        self.assertIsNone(record['first_unknown'])
        self.assertEqual(result['status'], 'uncertain')
        self.assertEqual(world.owner.members, {})
        self.assertFalse(any(call == 'kill' or type(call) is tuple and call[0] == 'killpg' for call in world.calls))

    def test_unsupported_pending_origin_stays_fail_closed_with_no_invented_reader_fact(self):
        world, observer, result = self.run_pair('pending_origin')
        record = observer.public_record()
        self.assertEqual(record['initial_command'], 'empty')
        self.assertEqual(result['status'], 'uncertain')
        self.assertIsNone(world.owner.leader)
        self.assertTrue(world.owner._leader_admission_refused)
        self.assertEqual(record['first_unknown']['reason'], 'leader_identity_unproven')
        self.assertIsNone(record['first_unknown']['command_equal'])
        self.assertIsNone(record['first_unknown']['command_empty'])
        self.assertEqual(world.owner.members, {})

    def test_exact_pending_admission_keeps_observer_initial_empty_without_first_unknown(self):
        world, observer, result = self.run_pair('pending_admitted')
        record = observer.public_record()
        self.assertEqual(record['initial_command'], 'empty')
        self.assertEqual(record['instrumentation_status'], 'captured')
        self.assertEqual(result['status'], 'completed')
        self.assertIsNone(record['first_unknown'])
        self.assertFalse(record['ownership_refusal_observed'])
        self.assertIsNone(world.owner._leader_pending)
        self.assertEqual(world.owner.members, {})

    def test_legacy_exact_source_and_maps_remain_bound_without_frozen_execution(self):
        source, module = legacy_runtime_metadata()
        canonical = ACTUAL_RUNTIME.execute_owned_process
        observer = self.make(World(), module=module, runtime_source=source)
        # Constructor reads code metadata only; no context or frozen body is entered.
        self.assertEqual(observer.public_record()['instrumentation_status'], 'ready')
        self.assertEqual(observer.public_record()['runtime_source_sha256'], LEGACY_RUNTIME_SHA)
        self.assertEqual(probe.RUNTIME_SHA256, LEGACY_RUNTIME_SHA)
        self.assertEqual(probe._SITES, {
            ('observe', 215): 'leader_identity_unproven',
            ('observe', 223): 'unregistered_without_live_leader',
            ('observe', 224): 'foreign_session_row',
            ('observe', 229): 'registered_identity_unproven',
            ('_terminate_owned', 309): 'termination_leader_unproven',
        })
        self.assertEqual(observer._sites, probe._SITES)
        self.assertEqual(probe._NEXT_LINES, {215: 216, 223: 224, 224: 210, 229: 226, 309: 310})
        self.assertEqual(observer._next_lines, probe._NEXT_LINES)
        self.assertEqual(probe._PHASES, {382: 'wait_observation', 401: 'interrupted_cleanup_observation', 404: 'final_observation'})
        self.assertEqual(observer._phases, probe._PHASES)
        self.assertIs(ACTUAL_RUNTIME.execute_owned_process, canonical)
        self.assertIsNone(sys.gettrace())

    def test_unrecognized_source_declines_without_inventing_a_recognized_source(self):
        world = World(); observer = self.make(world, runtime_source=RUNTIME + b'\n')
        before = sys.gettrace()
        with observer:
            self.assertIs(sys.gettrace(), before)
            result = world.execute()
        record = observer.public_record()
        self.assertEqual(record['instrumentation_status'], 'source_pin_mismatch')
        self.assertIsNone(record['runtime_source_sha256'])
        self.assertIsNone(record['first_unknown'])
        self.assertFalse(record['ownership_refusal_observed'])
        self.assertEqual(result['status'], 'uncertain')
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            self.assertFalse(observer.emit_failure(directory, call_failed=False))
            self.assertFalse((Path(directory) / probe.FILENAME).exists())

    def test_legacy_bytes_cannot_authorize_current_function_metadata(self):
        source, _ = legacy_runtime_metadata()
        observer = self.make(World(), runtime_source=source)
        with observer:
            self.assertIsNone(sys.gettrace())
        record = observer.public_record()
        self.assertEqual(record['instrumentation_status'], 'code_pin_mismatch')
        self.assertIsNone(record['first_unknown'])
        self.assertFalse(record['ownership_refusal_observed'])

    def test_current_registered_reader_error_uses_current_return_mapping(self):
        _, observer, result = self.run_pair('registered_error')
        self.assertEqual(result['status'], 'uncertain')
        first = observer.public_record()['first_unknown']
        self.assertEqual(first['reason'], 'registered_identity_unproven')
        self.assertEqual(first['member_identity'], 'reader_error')

    def test_five_first_unknown_callsite_reasons_and_wait_or_termination_phase(self):
        # Break: attributing a visited false predicate, or a later repeated add, as first cause.
        for mode, reason, relation, phase in (
            ('leader_mismatch', 'leader_identity_unproven', 'original_leader', 'wait_observation'),
            ('unregistered', 'unregistered_without_live_leader', 'other', 'wait_observation'),
            ('foreign_session', 'foreign_session_row', 'other', 'wait_observation'),
            ('registered_mismatch', 'registered_identity_unproven', 'registered_member', 'wait_observation'),
            ('termination', 'termination_leader_unproven', 'original_leader', 'termination_guard'),
        ):
            with self.subTest(mode=mode):
                world, observer, result = self.run_pair(mode)
                record = observer.public_record()
                self.assertEqual(record['first_unknown']['reason'], reason)
                self.assertEqual(record['first_unknown']['relation'], relation)
                self.assertEqual(record['first_unknown']['phase'], phase)
                self.assertIs(record['ownership_refusal_observed'], True)
                self.assertIs(type(world.owner.unknown), set)
                self.assertIs(world.owner.unknown, world.original_unknown)
                with patch.dict(ACTUAL_RUNTIME.__dict__, world.boundaries):
                    returned = world.owner.observe()
                self.assertIs(world.owner.unknown, world.original_unknown)
                self.assertIs(returned, world.original_unknown)

    def test_final_phase_and_initial_capture_shapes(self):
        for mode, capture in (('final', 'nonempty'), ('initial_empty', 'empty'), ('initial_error', 'absent')):
            with self.subTest(mode=mode):
                _, observer, _ = self.run_pair(mode)
                record = observer.public_record()
                self.assertEqual(record['initial_command'], capture)
                if mode == 'final':
                    self.assertEqual(record['first_unknown']['phase'], 'final_observation')

    def test_false_inline_add_conditions_do_not_create_unknown(self):
        _, observer, result = self.run_pair('registered_live')
        record = observer.public_record()
        self.assertEqual(record['first_unknown'], None)
        self.assertEqual(result['status'], 'uncertain')  # registered survivor, not unknown
        self.assertEqual(record['unknown_count'], 0)

    def test_member_inline_comparisons_do_not_invent_individual_reader_flags(self):
        world, observer, _ = self.run_pair('registered_mismatch')
        facts = observer.public_record()['first_unknown']
        self.assertIsNone(facts['row_session_equal'])
        self.assertIsNone(facts['birth_equal'])
        self.assertIsNone(facts['command_equal'])
        self.assertEqual(world.calls.count(('cmdline', 202)), 1, 'birth mismatch must keep original command short circuit')

    def test_original_interrupt_identity_and_boundary_calls_are_preserved(self):
        baseline = World('interrupt')
        with self.assertRaises(KeyboardInterrupt) as before:
            baseline.execute()
        current = World('interrupt'); observer = self.make(current)
        with self.assertRaises(KeyboardInterrupt) as after:
            with observer:
                current.execute()
        self.assertIs(before.exception, baseline.marker)
        self.assertIs(after.exception, current.marker)
        self.assertEqual(current.calls, baseline.calls)
        self.assertIsNone(sys.gettrace())

    def test_existing_trace_is_not_replaced_or_chained(self):
        world = World(); observer = self.make(world)
        def foreign(frame, event, arg):
            return None
        sys.settrace(foreign)
        try:
            with observer:
                self.assertIs(sys.gettrace(), foreign)
                world.execute()
            self.assertIs(sys.gettrace(), foreign)
        finally:
            sys.settrace(None)
        self.assertEqual(observer.public_record()['instrumentation_status'], 'existing_trace_conflict')

    def test_foreign_trace_installed_later_is_not_clobbered(self):
        world = World(); observer = self.make(world)
        def foreign(frame, event, arg):
            return None
        try:
            with observer:
                sys.settrace(foreign)
            self.assertIs(sys.gettrace(), foreign)
        finally:
            sys.settrace(None)
        self.assertEqual(observer.public_record()['instrumentation_status'], 'trace_changed_by_other')

    def test_source_ast_code_and_exact_test_selection_mismatch_declines_capture(self):
        def unrelated(*args):
            pass
        for changes in (
            {'runtime_source': RUNTIME + b'\n'},
            {'test_source': TEST_SOURCE.replace(b'assert calls==[True]', b'assert calls==[True, True]')},
            {'selected_callable': unrelated},
            {'nodeid': NODE.replace('after_canary_result]', 'after_canary_receipt]')},
            {'point': 'after_canary_receipt'},
        ):
            with self.subTest(changes=tuple(changes)):
                world = World(); observer = self.make(world, **changes)
                with observer:
                    self.assertIsNone(sys.gettrace())
                    result = world.execute()
                self.assertEqual(result['status'], 'uncertain')
                self.assertNotEqual(observer.public_record()['instrumentation_status'], 'captured')
        world = World()
        with patch.object(world.module._OwnedGroup, 'observe', unrelated):
            observer = self.make(world)
            with observer:
                self.assertIsNone(sys.gettrace())
        self.assertEqual(observer.public_record()['instrumentation_status'], 'code_pin_mismatch')

    def test_privacy_no_raw_frames_values_and_bounded_output(self):
        _, observer, _ = self.run_pair('leader_mismatch')
        encoded = json.dumps(observer.public_record(), sort_keys=True)
        self.assertLessEqual(len(encoded.encode()), 4096)
        for value in ('SECRET', '/private', '101', '202', 'private-path', 'original-command', 'pid', 'stdout', 'stderr'):
            self.assertNotIn(value, encoded)
        def safe(value):
            if type(value) in (type(None), bool, int, str, bytes):
                return
            if type(value) in (list, tuple):
                for item in value: safe(item)
                return
            if type(value) is dict:
                for key, item in value.items(): safe(key); safe(item)
                return
            # The observer keeps code objects and its own bound callback, not live frame/process/exception data.
            from types import CodeType, MethodType
            self.assertIn(type(value), (CodeType, MethodType))
        for name, value in vars(observer).items():
            if name not in ('_codes', '_installed_trace'):
                safe(value)

    def test_failure_only_exclusive_sanitized_writer_and_io_failure(self):
        _, observer, _ = self.run_pair('leader_mismatch')
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            path = Path(directory) / 'canary-unknown-after-result.json'
            self.assertFalse(observer.emit_failure(directory, call_failed=False))
            self.assertFalse(path.exists())
            self.assertTrue(observer.emit_failure(directory, call_failed=True))
            original = path.read_bytes()
            self.assertFalse(observer.emit_failure(directory, call_failed=True))
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(json.loads(original)['first_unknown']['reason'], 'leader_identity_unproven')
            self.assertFalse(observer.emit_failure(str(path / 'bad'), call_failed=True))

    def test_event_budget_and_callback_error_disable_diagnostics_without_altering_execution(self):
        for changes, status in (({'max_events': 2}, 'event_budget_exceeded'), ({'max_owners': 0}, 'owner_budget_exceeded')):
            baseline = World(); expected = baseline.execute()
            current = World(); observer = self.make(current, **changes)
            with observer:
                result = current.execute()
            self.assertEqual(current.calls, baseline.calls)
            self.assertEqual(result, expected)
            self.assertEqual(observer.public_record()['instrumentation_status'], status)
        baseline = World(); expected = baseline.execute()
        current = World(); observer = self.make(current)
        with observer:
            with patch.object(type(observer), '_event', side_effect=RuntimeError('SECRET callback failure')):
                result = current.execute()
        self.assertEqual(current.calls, baseline.calls)
        self.assertEqual(result, expected)
        self.assertEqual(observer.public_record()['instrumentation_status'], 'callback_failed')

    def test_completed_runtime_cannot_emit_an_ownership_failure_artifact(self):
        # Break: emitting an ownership diagnostic for an unrelated later assertion failure.
        _, observer, result = self.run_pair('completed')
        self.assertEqual(result['status'], 'completed')
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            self.assertFalse(observer.emit_failure(directory, call_failed=True))
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_encoding_or_output_writer_failure_cannot_escape(self):
        # Break: JSON encoding sits outside the best-effort failure writer boundary.
        _, observer, _ = self.run_pair('leader_mismatch')
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            with patch.object(probe.json, 'dumps', side_effect=RuntimeError('SECRET encoding')):
                self.assertFalse(observer.emit_failure(directory, call_failed=True))
            with patch.object(probe.os, 'open', side_effect=RuntimeError('SECRET write')):
                self.assertFalse(observer.emit_failure(directory, call_failed=True))

    def test_nested_observer_does_not_steal_outer_trace(self):
        world = World(); outer, inner = self.make(world), self.make(world)
        with outer:
            original_trace = sys.gettrace()
            with inner:
                self.assertIs(sys.gettrace(), original_trace)
                result = world.execute()
            self.assertIs(sys.gettrace(), original_trace)
        self.assertEqual(result['status'], 'uncertain')
        self.assertEqual(inner.public_record()['instrumentation_status'], 'existing_trace_conflict')
        self.assertEqual(outer.public_record()['first_unknown']['reason'], 'leader_identity_unproven')

    def test_equal_but_distinct_code_and_unrelated_owner_are_not_adopted(self):
        world = World(); observer = self.make(world)
        original = ACTUAL_RUNTIME._OwnedGroup.observe.__code__
        clone = original.replace()
        self.assertEqual(clone, original)
        self.assertIsNone(observer._role(clone))
        unrelated = World('completed')
        with observer:
            with patch.dict(ACTUAL_RUNTIME.__dict__, unrelated.boundaries):
                owner = ACTUAL_RUNTIME._OwnedGroup(unrelated.process, unrelated.boundaries['_optional_psutil']())
                exact_set = owner.unknown
                owner.observe()
                self.assertIs(owner.unknown, exact_set)
            result = world.execute()
        self.assertEqual(result['status'], 'uncertain')
        self.assertEqual(observer.public_record()['owner_count'], 1)

    def test_second_original_execution_overflow_does_not_prevent_original_calls(self):
        world = World(); other = World('completed'); observer = self.make(world)
        with observer:
            first = world.execute()
            second = other.execute()
        self.assertEqual(first['status'], 'uncertain')
        self.assertEqual(second['status'], 'completed')
        self.assertEqual(observer.public_record()['instrumentation_status'], 'owner_budget_exceeded')
        self.assertEqual(observer.public_record()['owner_count'], 2)

    def test_no_process_data_retained_even_while_original_frames_are_active(self):
        world = World(); observer = self.make(world)
        original_group = world.boundaries['_group_members']
        def group(identifier):
            def primitive(value):
                if type(value) in (type(None), bool, int, str, bytes): return True
                if type(value) in (tuple, list): return all(primitive(item) for item in value)
                if type(value) is dict: return all(primitive(key) and primitive(item) for key, item in value.items())
                return False
            self.assertTrue(primitive(observer._frames))
            return original_group(identifier)
        world.boundaries['_group_members'] = group
        with observer:
            result = world.execute()
        self.assertEqual(result['status'], 'uncertain')
        self.assertEqual(observer._frames, {})

    def test_hostile_command_values_are_never_stringified_or_compared_by_observer(self):
        class Hostile:
            def __eq__(self, other): raise AssertionError('extra equality')
            def __str__(self): raise AssertionError('extra str')
            def __repr__(self): raise AssertionError('extra repr')
        self.assertIsNone(probe._command_shape((Hostile(),)))
        self.assertIsNone(probe._command_shape(tuple('x' for _ in range(129))))
        self.assertIsNone(probe._command_shape(('x' * 4097,)))

    def test_callback_dispatch_lookup_failure_cannot_interrupt_original_execution(self):
        baseline = World(); expected = baseline.execute()
        current = World(); observer = self.make(current)
        with observer:
            with patch.object(type(observer), '_role', side_effect=RuntimeError('SECRET dispatch lookup')):
                result = current.execute()
        self.assertEqual(result, expected)
        self.assertEqual(current.calls, baseline.calls)
        self.assertEqual(observer.public_record()['instrumentation_status'], 'callback_failed')

    def test_writer_close_failure_cannot_replace_original_failure(self):
        _, observer, _ = self.run_pair('leader_mismatch')
        close = probe.os.close
        descriptors = []
        def failing_close(descriptor):
            descriptors.append(descriptor)
            raise RuntimeError('SECRET close')
        try:
            with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
                with patch.object(probe.os, 'close', failing_close):
                    self.assertFalse(observer.emit_failure(directory, call_failed=True))
        finally:
            for descriptor in descriptors:
                close(descriptor)

    def test_same_metadata_changed_test_body_cannot_borrow_original_source_pin(self):
        changed_source = TEST_SOURCE.replace(b'calls==[True]', b'calls==[False]')
        changed_code = source_code(compile(changed_source, '<changed-test>', 'exec'), SELECTED.__name__)
        changed = FunctionType(changed_code, {})
        world = World()
        observer = self.make(world, selected_callable=changed, canonical_test_callable=changed)
        with observer:
            self.assertIsNone(sys.gettrace())
            world.execute()
        self.assertEqual(observer.public_record()['instrumentation_status'], 'test_callable_pin_mismatch')

    def test_foreign_trace_installed_inside_runtime_disables_old_local_callbacks(self):
        world = World(); observer = self.make(world)
        group = world.boundaries['_group_members']
        def foreign(frame, event, arg): return None
        def change_trace(identifier):
            sys.settrace(foreign)
            return group(identifier)
        world.boundaries['_group_members'] = change_trace
        try:
            with observer:
                result = world.execute()
                self.assertIs(sys.gettrace(), foreign)
            self.assertIs(sys.gettrace(), foreign)
        finally:
            sys.settrace(None)
        self.assertEqual(result['status'], 'uncertain')
        self.assertEqual(observer.public_record()['instrumentation_status'], 'trace_changed_by_other')
        self.assertIsNone(observer.public_record()['first_unknown'])

    def test_observer_instance_is_one_shot_and_cannot_reuse_prior_cause(self):
        world = World(); observer = self.make(world)
        with observer:
            world.execute()
        second = World('completed')
        with observer:
            self.assertIsNone(sys.gettrace())
            result = second.execute()
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(observer.public_record()['instrumentation_status'], 'observer_reused')
        self.assertIsNone(observer.public_record()['first_unknown'])
        self.assertFalse(observer.public_record()['ownership_refusal_observed'])

    def test_missing_next_line_mapping_does_not_invent_a_first_add(self):
        baseline = World(); expected = baseline.execute()
        current = World(); observer = self.make(current)
        site = next(line for (role, line), reason in observer._sites.items()
                    if role == 'observe' and reason == 'leader_identity_unproven')
        with patch.dict(observer._next_lines, {site: 999}):
            with observer:
                result = current.execute()
        self.assertEqual(result, expected)
        self.assertEqual(current.calls, baseline.calls)
        record = observer.public_record()
        self.assertIsNone(record['first_unknown'])
        self.assertTrue(record['transition_unconfirmed'])

    def test_duplicate_adds_keep_first_reason_and_multiple_members_keep_count(self):
        for mode, count in (('duplicate', 1), ('two_unknown', 2)):
            with self.subTest(mode=mode):
                _, observer, _ = self.run_pair(mode)
                record = observer.public_record()
                self.assertEqual(record['first_unknown']['reason'], 'unregistered_without_live_leader')
                self.assertEqual(record['unknown_count'], count)

    def test_exit_empty_and_zombie_observation_keep_original_completion_rules(self):
        for mode in ('exit_empty', 'zombie'):
            with self.subTest(mode=mode):
                _, observer, result = self.run_pair(mode)
                self.assertEqual(result['status'], 'completed')
                self.assertIsNone(observer.public_record()['first_unknown'])
        _, observer, _ = self.run_pair('empty_then_changed')
        self.assertFalse(observer.public_record()['first_unknown']['command_equal'])

    def test_group_session_and_birth_failures_do_not_invent_missing_local_comparisons(self):
        for mode in ('changed_group', 'changed_session', 'changed_birth'):
            with self.subTest(mode=mode):
                world, observer, _ = self.run_pair(mode)
                facts = observer.public_record()['first_unknown']
                self.assertEqual(facts['reason'], 'leader_identity_unproven')
                if mode == 'changed_birth':
                    self.assertFalse(facts['birth_equal'])
                    self.assertTrue(facts['command_equal'])
                else:
                    self.assertIsNone(facts['birth_equal'])
                    self.assertIsNone(facts['command_equal'])
                    self.assertEqual(world.calls.count(('cmdline', 101)), 1)

    def test_later_enrollment_reader_cannot_become_the_final_pre_scan_fact(self):
        _, observer, _ = self.run_pair('enrollment_changed')
        facts = observer.public_record()['first_unknown']
        self.assertEqual(facts['reason'], 'unregistered_without_live_leader')
        self.assertEqual(facts['phase'], 'final_observation')
        self.assertIsNone(facts['birth_equal'])
        self.assertIsNone(facts['command_equal'])
        _, observer, _ = self.run_pair('registered_error')
        self.assertEqual(observer.public_record()['first_unknown']['member_identity'], 'reader_error')

    def test_constructor_unwind_and_callback_failure_preserve_original_interrupt(self):
        baseline = World('constructor_interrupt')
        with self.assertRaises(KeyboardInterrupt): baseline.execute()
        current = World('constructor_interrupt'); observer = self.make(current)
        with self.assertRaises(KeyboardInterrupt) as caught:
            with observer: current.execute()
        self.assertIs(caught.exception, current.marker)
        self.assertEqual(current.calls, baseline.calls)
        self.assertEqual(observer.public_record()['initial_command'], 'not_captured')
        self.assertIsNone(observer.public_record()['first_unknown'])
        current = World('interrupt'); observer = self.make(current)
        with self.assertRaises(KeyboardInterrupt) as caught:
            with observer:
                with patch.object(type(observer), '_event', side_effect=RuntimeError('SECRET callback')):
                    current.execute()
        self.assertIs(caught.exception, current.marker)
        self.assertIsNone(sys.gettrace())

    def test_actual_handle_quarantine_stays_sticky_without_another_popen(self):
        def run(world, observer=None):
            handle = ACTUAL_RUNTIME.CancellableExecution()
            with observer if observer is not None else nullcontext():
                with handle.running() as event:
                    result = world.execute(cancel_event=event)
            self.assertIs(handle._quarantine, world.owner)
            before = list(world.calls)
            with self.assertRaisesRegex(RuntimeError, 'remains unreconciled'):
                with handle.running():
                    self.fail('unknown cannot authorize a second execution')
            self.assertEqual(world.calls, before)
            self.assertEqual(world.calls.count('Popen'), 1)
            return result
        baseline = World(); expected = run(baseline)
        current = World(); observer = self.make(current)
        result = run(current, observer)
        self.assertEqual(result, expected)
        self.assertEqual(current.calls, baseline.calls)

    def test_same_instance_nested_reentry_keeps_outer_trace_until_outer_exit(self):
        world = World(); observer = self.make(world)
        with observer:
            own = sys.gettrace()
            with observer:
                self.assertIs(sys.gettrace(), own)
                world.execute()
            self.assertIs(sys.gettrace(), own)
        self.assertIsNone(sys.gettrace())
        self.assertEqual(observer.public_record()['instrumentation_status'], 'observer_reentry_conflict')

    def test_original_pytest_rewritten_callable_uses_its_collection_code_anchor(self):
        from _pytest.assertion.rewrite import rewrite_asserts
        tree = ast.parse(TEST_SOURCE)
        rewrite_asserts(tree, TEST_SOURCE)
        rewritten_code = source_code(compile(tree, '<pytest-rewritten-original>', 'exec'), SELECTED.__name__)
        rewritten = FunctionType(rewritten_code, {})
        self.assertNotEqual(rewritten_code.co_code, SELECTED.__code__.co_code)
        world = World()
        observer = self.make(world, selected_callable=rewritten, canonical_test_callable=rewritten,
                             selected_code_anchor=rewritten.__code__)
        with observer:
            world.execute()
        self.assertEqual(observer.public_record()['instrumentation_status'], 'captured')
        self.assertEqual(observer.public_record()['first_unknown']['reason'], 'leader_identity_unproven')

    def test_canonical_methods_stay_original_while_runtime_frames_are_active(self):
        methods = {name: getattr(ACTUAL_RUNTIME._OwnedGroup, name) for name in ('__init__', 'observe', 'leader_matches', 'member_state')}
        execute, terminate = ACTUAL_RUNTIME.execute_owned_process, ACTUAL_RUNTIME._terminate_owned
        model_modules = {name: sys.modules.get(name) for name in ('torch', 'ultralytics', 'onnxruntime')}
        world = World(); observer = self.make(world)
        group = world.boundaries['_group_members']
        def checked(identifier):
            for name, original in methods.items():
                self.assertIs(getattr(ACTUAL_RUNTIME._OwnedGroup, name), original)
            self.assertIs(ACTUAL_RUNTIME.execute_owned_process, execute)
            self.assertIs(ACTUAL_RUNTIME._terminate_owned, terminate)
            self.assertIs(world.owner.unknown, world.original_unknown)
            return group(identifier)
        world.boundaries['_group_members'] = checked
        with observer:
            result = world.execute()
        self.assertEqual(result['status'], 'uncertain')
        for name, original in model_modules.items():
            self.assertIs(sys.modules.get(name), original, 'control imported or replaced a model module')

    def test_original_observe_and_execute_return_objects_cross_trace_unchanged(self):
        # Witness only the diagnostic callback; never wrap or replace a runtime method.
        baseline = World(); expected = baseline.execute()
        world = World(); observer = self.make(world)
        original_event = type(observer)._event
        observed_sets, execute_return_ids = [], []
        def witness(instance, role, frame, event, arg):
            if event == 'return' and role in ('observe', '_observe_owned'):
                observed_sets.append(arg is world.original_unknown and type(arg) is set)
            if event == 'return' and role == 'execute_owned_process' and type(arg) is dict:
                execute_return_ids.append(id(arg))
            return original_event(instance, role, frame, event, arg)
        with patch.object(type(observer), '_event', witness):
            with observer:
                result = world.execute()
        self.assertTrue(observed_sets)
        self.assertTrue(all(observed_sets))
        self.assertEqual(execute_return_ids, [id(result)])
        self.assertEqual(result, expected)
        self.assertEqual(world.calls, baseline.calls)
        self.assertEqual(observer.public_record()['instrumentation_status'], 'captured')


class AdapterLifecycleTests(unittest.TestCase):
    """Model pytest items/reports, never collect or call the original real canary."""
    def adapter(self):
        spec = importlib.util.spec_from_file_location('canary_adapter_lifecycle_control', ROOT/'backend/tests/conftest.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertTrue(callable(getattr(module, 'pytest_collection_modifyitems', None)), 'test-scoped adapter is not integrated')
        return module

    def item(self, *, nodeid=NODE, point='after_canary_result'):
        import pytest
        return SimpleNamespace(nodeid=nodeid, obj=SELECTED,
            module=SimpleNamespace(**{SELECTED.__name__: SELECTED}),
            callspec=SimpleNamespace(params={'point': point}), stash=pytest.Stash())

    def report(self, adapter, item, *, when='call', failed=True):
        report = SimpleNamespace(when=when, failed=failed)
        before = dict(vars(report))
        calls = []
        def result():
            calls.append(True); return report
        hook = adapter.pytest_runtest_makereport(item, None)
        next(hook)
        with self.assertRaises(StopIteration):
            hook.send(SimpleNamespace(get_result=result))
        self.assertEqual(calls, [True])
        self.assertEqual(vars(report), before, 'adapter changed the original pytest report')

    def open_fixture(self, adapter, item):
        fixture = adapter._capture_original_canary_unknown.__wrapped__
        generator = fixture(SimpleNamespace(node=item, function=item.obj))
        next(generator)
        return generator

    def close_fixture(self, generator):
        with self.assertRaises(StopIteration):
            next(generator)

    def test_failed_call_anchors_original_code_reports_then_writes_after_trace_cleanup(self):
        adapter = self.adapter(); item = self.item()
        adapter.pytest_collection_modifyitems([item])
        self.assertIs(item.stash[adapter._CANARY_ORIGINAL_CODE], SELECTED.__code__)
        world = World(); writes = []
        original_writer = probe.Observer.emit_failure
        def writer(observer, directory, *, call_failed):
            writes.append((sys.gettrace() is None, call_failed))
            return original_writer(observer, directory, call_failed=call_failed)
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            with patch.dict('os.environ', {'MV_CI_RECORD_DIR': directory}):
                generator = self.open_fixture(adapter, item)
                self.assertIsNotNone(sys.gettrace())
                result = world.execute()
                self.assertEqual(result['status'], 'uncertain')
                path = Path(directory)/probe.FILENAME
                self.assertFalse(path.exists(), 'writer ran before the original call report')
                self.report(adapter, item, when='setup', failed=False)
                self.assertFalse(item.stash.get(adapter._CANARY_CALL_FAILED, False))
                self.report(adapter, item, failed=True)
                self.assertTrue(item.stash[adapter._CANARY_CALL_FAILED])
                with patch.object(probe.Observer, 'emit_failure', writer):
                    self.close_fixture(generator)
                self.assertIsNone(sys.gettrace())
                record = json.loads(path.read_bytes())
                self.assertEqual(record['first_unknown']['reason'], 'leader_identity_unproven')
                self.assertTrue(record['ownership_refusal_observed'])
        self.assertEqual(writes, [(True, True)])

    def test_pass_completed_result_and_other_parameter_cannot_emit_false_cause(self):
        for mode, failed, nodeid, point in (
            ('leader_mismatch', False, NODE, 'after_canary_result'),
            ('completed', True, NODE, 'after_canary_result'),
            ('leader_mismatch', True, NODE, 'after_canary_receipt'),
            ('leader_mismatch', True, NODE.replace('after_canary_result]', 'after_canary_receipt]'), 'after_canary_receipt'),
        ):
            with self.subTest(mode=mode, failed=failed, point=point):
                adapter = self.adapter(); item = self.item(nodeid=nodeid, point=point)
                adapter.pytest_collection_modifyitems([item])
                with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
                    with patch.dict('os.environ', {'MV_CI_RECORD_DIR': directory}):
                        generator = self.open_fixture(adapter, item)
                        if point != 'after_canary_result': self.assertIsNone(sys.gettrace())
                        World(mode).execute()
                        self.report(adapter, item, failed=failed)
                        self.close_fixture(generator)
                        self.assertIsNone(sys.gettrace())
                        self.assertFalse((Path(directory)/probe.FILENAME).exists())

    def test_collection_anchor_rejects_later_same_function_code_change_without_running_body(self):
        adapter = self.adapter(); item = self.item()
        adapter.pytest_collection_modifyitems([item])
        original = SELECTED.__code__
        changed = source_code(compile(TEST_SOURCE.replace(b'calls==[True]', b'calls==[False]'), '<changed-selected-test>', 'exec'), SELECTED.__name__)
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            with patch.dict('os.environ', {'MV_CI_RECORD_DIR': directory}):
                SELECTED.__code__ = changed
                try:
                    generator = self.open_fixture(adapter, item)
                    self.assertIsNone(sys.gettrace())
                    self.assertIs(item.stash[adapter._CANARY_ORIGINAL_CODE], original)
                    World().execute()
                    self.report(adapter, item, when='teardown', failed=True)
                    self.assertFalse(item.stash.get(adapter._CANARY_CALL_FAILED, False))
                    self.report(adapter, item, failed=True)
                    self.close_fixture(generator)
                    record = json.loads((Path(directory)/probe.FILENAME).read_bytes())
                    self.assertEqual(record['instrumentation_status'], 'test_callable_pin_mismatch')
                    self.assertIsNone(record['first_unknown'])
                    self.assertFalse(record['ownership_refusal_observed'])
                finally:
                    SELECTED.__code__ = original
        self.assertIs(SELECTED.__code__, original)


if __name__ == '__main__':
    unittest.main()
