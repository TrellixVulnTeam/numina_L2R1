#
# Copyright 2008-2012 Universidad Complutense de Madrid
# 
# This file is part of Numina
# 
# Numina is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# 
# Numina is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
# 
# You should have received a copy of the GNU General Public License
# along with Numina.  If not, see <http://www.gnu.org/licenses/>.
# 

import shutil
import copy

import numpy # pylint: disable-msgs=E1101
import pyfits

from ..array import resize_array 

def compute_median(img, mask, region):
    d = img.data[region]
    m = mask.data[region]
    value = numpy.median(d[m == 0])
    return value, img

def get_image_shape(header):
    ndim = header['naxis']
    return tuple(header.get('NAXIS%d' % i) for i in range(1, ndim + 1))

def custom_slice_to_str(slc):
    if slc.step is None:
        return '%i:%i' % (slc.start, slc.stop)
    else:
        return '%i:%i:%i' % (slc.start, slc.stop, slc.step)

def custom_region_to_str(region):
    jints = [custom_slice_to_str(slc) for slc in region]
    return '[' + ','.join(jints) + ']'

def resize_hdu(hdu, newshape, region, fill=0.0, scale=1):
    basedata = hdu.data
    newdata = resize_array(basedata, newshape, region, fill=fill, scale=scale)
    hdu.header.update('NVALREGI', custom_region_to_str(region), 
                      'Valid region of resized FITS')          
    newhdu = pyfits.PrimaryHDU(newdata, hdu.header)                
    return newhdu

def resize_fits(fitsfile, newfilename, newshape, region, scale=1, fill=0.0, clobber=True):
    
    close_on_exit = False
    if isinstance(fitsfile, basestring):
        hdulist = pyfits.open(fitsfile, mode='readonly')
        close_on_exit = True
    else:
        hdulist = fitsfile
        
    try:
        hdu = hdulist['primary']
        newhdu = resize_hdu(hdu, newshape, region, fill=fill, scale=scale)
        newhdu.writeto(newfilename, clobber=clobber)
    finally:
        if close_on_exit:
            hdulist.close()



