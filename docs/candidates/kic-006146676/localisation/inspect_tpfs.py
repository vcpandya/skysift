import sys, glob, numpy as np, warnings
from astropy.io import fits
from astropy.wcs import WCS
warnings.simplefilter("ignore")
cand=(297.868708651717,41.45556694086529); targ=(297.8645492592465,41.45491468089151)
for f in sorted(glob.glob(sys.argv[1]+"/*.fits.gz")):
    with fits.open(f) as h:
        q=h[0].header["QUARTER"]; s=h[0].header.get("SEASON"); m=h[0].header.get("MODULE"); o=h[0].header.get("OUTPUT")
        fl=h[1].data["FLUX"]; ap=h[2].data; w=WCS(h[2].header)
        pc=w.all_world2pix([cand[0]],[cand[1]],0); pt=w.all_world2pix([targ[0]],[targ[1]],0)
        print(q,s,m,o,fl.shape,"cand(col,row)=%.2f,%.2f targ=%.2f,%.2f"%(pc[0][0],pc[1][0],pt[0][0],pt[1][0]),
              "apmask_opt=",int(((ap&2)>0).sum()), "nanpix",int(np.all(~np.isfinite(fl),axis=0).sum()))
