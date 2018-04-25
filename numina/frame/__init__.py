#
# Copyright 2008-2014 Universidad Complutense de Madrid
#
# This file is part of Numina
#
# SPDX-License-Identifier: GPL-3.0+
# License-Filename: LICENSE.txt
#

import warnings
import six
from astropy.io import fits

import numina.array


def get_hdu_shape(header):
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


def resize_hdu(hdu, newshape, region, window=None, fill=0.0,
               scale=1, conserve=True, dtype=None):
    from numina.array import resize_array

    basedata = hdu.data
    newdata = numina.array.resize_array(
        basedata, newshape, region, window=window,
        fill=fill, scale=scale, conserve=conserve,
        dtype=dtype
    )
    hdu.header['NVALREGI'] = (custom_region_to_str(region),
                              'Valid region of resized FITS')
    if window:
        hdu.header['OVALREGI'] = (custom_region_to_str(window),
                                  'Valid region of original FITS')
    newhdu = fits.PrimaryHDU(newdata, hdu.header)
    return newhdu


def resize_hdul(hdul, newshape, region, extensions=None, window=None,
                scale=1, fill=0.0, conserve=True):
    from numina.frame import resize_hdu
    if extensions is None:
        extensions = [0]

    nhdul = [None] * len(hdul)
    for ext, hdu in enumerate(hdul):
        if ext in extensions:
            nhdul[ext] = resize_hdu(hdu, newshape,
                                    region, fill=fill,
                                    window=window,
                                    scale=scale,
                                    conserve=conserve)
        else:
            nhdul[ext] = hdu
    return fits.HDUList(nhdul)


def resize_fits(fitsfile, newfilename, newshape, region, window=None,
                scale=1, fill=0.0, clobber=True, conserve=True, dtype=None):

    close_on_exit = False
    if isinstance(fitsfile, six.string_types):
        hdulist = fits.open(fitsfile, mode='readonly')
        close_on_exit = True
    else:
        hdulist = fitsfile

    try:
        hdu = hdulist['primary']
        newhdu = resize_hdu(hdu, newshape, region, fill=fill,
                            window=window, scale=scale, conserve=conserve,
                            dtype=dtype)
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            newhdu.writeto(newfilename, clobber=clobber)
    finally:
        if close_on_exit:
            hdulist.close()
