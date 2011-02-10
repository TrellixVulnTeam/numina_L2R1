#
# Copyright 2008-2011 Sergio Pascual
# 
# This file is part of PyEmir
# 
# PyEmir is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# 
# PyEmir is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
# 
# You should have received a copy of the GNU General Public License
# along with PyEmir.  If not, see <http://www.gnu.org/licenses/>.
#

'''Recipe for the reduction of imaging mode observations.'''

import logging
import os.path
import shutil
import itertools

import numpy
import pyfits
import scipy.ndimage as ndimage
import scipy.signal

import numina.image
import numina.qa
from numina.image import DiskImage
from numina.image.imsurfit import imsurfit
from numina.logger import captureWarnings
from numina.array import subarray_match
from numina.array import combine_shape, resize_array, correct_flatfield
from numina.array import fixpix
from numina.array import compute_median_background, compute_sky_advanced, create_object_mask
from numina.array import SextractorConf
from numina.array.combine import flatcombine, combine
from numina.array.combine import median
from numina.recipes import RecipeBase, RecipeError
from numina.recipes.registry import ProxyPath, ProxyQuery
from numina.recipes.registry import Schema
from emir.dataproducts import create_result, create_raw
from emir.recipes import EmirRecipeMixin

_logger = logging.getLogger("emir.recipes")

def _name_redimensioned_images(label, iteration, ext='.fits'):
    dn = '%s_r_i%01d%s' % (label, iteration, ext)
    mn = '%s_mr_i%01d%s' % (label, iteration, ext)
    return dn, mn

def _name_object_mask(label, iteration, ext='.fits'):
    return '%s_mro_i%01d%s' % (label, iteration, ext)

def _name_skyflat_proc(label, iteration, ext='.fits'):
    dn = '%s_rf_i%01d%s' % (label, iteration, ext)
    return dn

def _name_skybackground(label, iteration, ext='.fits'):
    dn = '%s_sky_i%01d%s' % (label, iteration, ext)
    return dn

def _name_skybackgroundmask(label, iteration, ext='.fits'):
    dn = '%s_skymask_i%01d%s' % (label, iteration, ext)
    return dn


def _name_skysub_proc(label, iteration, ext='.fits'):
    dn = '%s_rfs_i%01d%s' % (label, iteration, ext)
    return dn

def _name_skyflat(label, iteration, ext='.fits'):
    dn = 'superflat_%s_i%01d%s' % (label, iteration, ext)
    return dn

def _name_segmask(iteration, ext='.fits'):
    return "check_i%01d%s" % (iteration, ext)
    
def get_image_shape(header):
    ndim = header['naxis']
    return tuple(header.get('NAXIS%d' % i) for i in range(1, ndim + 1))



def resize_hdu(hdu, newshape, region):
    basedata = hdu.data
    newdata = resize_array(basedata, newshape, region)                
    newhdu = pyfits.PrimaryHDU(newdata, hdu.header)                
    return newhdu

def resize_fits(fitsfile, newfilename, newshape, region):
    
    close_on_exit = False
    if isinstance(fitsfile, basestring):
        hdulist = pyfits.open(fitsfile, mode='readonly')
        close_on_exit = True
    else:
        hdulist = fitsfile
        
    try:
        hdu = hdulist['primary']
        newhdu = resize_hdu(hdu, newshape, region)
        newhdu.writeto(newfilename)
    finally:
        if close_on_exit:
            hdulist.close()

def update_sky_related(images, nimages=5):
    
    nox = nimages

    for idx, image in enumerate(images[:nox]):
        bw = images[0:2 * nox + 1][:idx]
        fw = images[0:2 * nox + 1][idx + 1:]
        image.sky_related = (bw, fw)

    for idx, image in enumerate(images[nox:-nox]):
        bw = images[idx:idx + nox]
        fw = images[idx + nox + 1:idx + 2 * nox + 1]
        image.sky_related = (bw, fw)
 
    for idx, image in enumerate(images[-nox:]):
        bw = images[-2 * nox - 1:][0:idx + nox + 1]
        fw = images[-2 * nox - 1:][nox + idx + 2:]
        image.sky_related = (bw, fw)
    
    return images

def sgolay2d ( z, window_size, order, derivative=None):
    """
    """
    
    # http://www.scipy.org/Cookbook/SavitzkyGolay
    
    # number of terms in the polynomial expression
    n_terms = ( order + 1 ) * ( order + 2)  / 2.0

    if  window_size % 2 == 0:
        raise ValueError('window_size must be odd')

    if window_size**2 < n_terms:
        raise ValueError('order is too high for the window size')

    half_size = window_size // 2

    # exponents of the polynomial. 
    # p(x,y) = a0 + a1*x + a2*y + a3*x^2 + a4*y^2 + a5*x*y + ... 
    # this line gives a list of two item tuple. Each tuple contains 
    # the exponents of the k-th term. First element of tuple is for x
    # second element for y.
    # Ex. exps = [(0,0), (1,0), (0,1), (2,0), (1,1), (0,2), ...]
    exps = [ (k-n, n) for k in range(order+1) for n in range(k+1) ]

    # coordinates of points
    ind = numpy.arange(-half_size, half_size+1, dtype=numpy.float64)
    dx = numpy.repeat( ind, window_size )
    dy = numpy.tile( ind, [window_size, 1]).reshape(window_size**2, )

    # build matrix of system of equation
    A = numpy.empty( (window_size**2, len(exps)) )
    for i, exp in enumerate( exps ):
        A[:,i] = (dx**exp[0]) * (dy**exp[1])

    # pad input array with appropriate values at the four borders
    new_shape = z.shape[0] + 2*half_size, z.shape[1] + 2*half_size
    Z = numpy.zeros( (new_shape) )
    # top band
    band = z[0, :]
    Z[:half_size, half_size:-half_size] =  band -  numpy.abs( numpy.flipud( z[1:half_size+1, :] ) - band )
    # bottom band
    band = z[-1, :]
    Z[-half_size:, half_size:-half_size] = band  + numpy.abs( numpy.flipud( z[-half_size-1:-1, :] )  -band )
    # left band
    band = numpy.tile( z[:,0].reshape(-1,1), [1,half_size])
    Z[half_size:-half_size, :half_size] = band - numpy.abs( numpy.fliplr( z[:, 1:half_size+1] ) - band )
    # right band
    band = numpy.tile( z[:,-1].reshape(-1,1), [1,half_size] )
    Z[half_size:-half_size, -half_size:] =  band + numpy.abs( numpy.fliplr( z[:, -half_size-1:-1] ) - band )
    # central band
    Z[half_size:-half_size, half_size:-half_size] = z

    # top left corner
    band = z[0,0]
    Z[:half_size,:half_size] = band - numpy.abs( numpy.flipud(numpy.fliplr(z[1:half_size+1,1:half_size+1]) ) - band )
    # bottom right corner
    band = z[-1,-1]
    Z[-half_size:,-half_size:] = band + numpy.abs( numpy.flipud(numpy.fliplr(z[-half_size-1:-1,-half_size-1:-1]) ) - band )

    # top right corner
    band = Z[half_size,-half_size:]
    Z[:half_size,-half_size:] = band - numpy.abs( numpy.flipud(Z[half_size+1:2*half_size+1,-half_size:]) - band )
    # bottom left corner
    band = Z[-half_size:,half_size].reshape(-1,1)
    Z[-half_size:,:half_size] = band - numpy.abs( numpy.fliplr(Z[-half_size:, half_size+1:2*half_size+1]) - band )

    # solve system and convolve
    if derivative == None:
        m = numpy.linalg.pinv(A)[0].reshape((window_size, -1))
        return scipy.signal.fftconvolve(Z, m, mode='valid')
    elif derivative == 'col':
        c = numpy.linalg.pinv(A)[1].reshape((window_size, -1))
        return scipy.signal.fftconvolve(Z, -c, mode='valid')
    elif derivative == 'row':
        r = numpy.linalg.pinv(A)[2].reshape((window_size, -1))
        return scipy.signal.fftconvolve(Z, -r, mode='valid')
    elif derivative == 'both':
        c = numpy.linalg.pinv(A)[1].reshape((window_size, -1))
        r = numpy.linalg.pinv(A)[2].reshape((window_size, -1))
        return scipy.signal.fftconvolve(Z, -r, mode='valid'), scipy.signal.fftconvolve(Z, -c, mode='valid')

class FitOne(object):
    def __init__(self, x,y,z):
        '''Fit a plane to a region using least squares.'''
        x = x.astype('float')
        y = y.astype('float')
        self.xm = x.mean()
        self.ym = y.mean()
        x -= self.xm
        y -= self.ym

        aa = numpy.zeros(shape=(3,3))
        aa[0,0] = 1
        aa[1,1] = (x*x).mean()
        aa[2,2] = (y*y).mean()
        aa[1,2] = aa[2,1] = (x*y).mean()
        bb = [z.mean(), (x*z).mean(), (y*z).mean()]

        self.pf = numpy.linalg.solve(aa, bb)

    def __call__(self, x, y):
        xf = x.astype('float') - self.xm
        yf = y.astype('float') - self.ym
        return self.pf[0] + self.pf[1] * xf + self.pf[2] * yf

def fixpix2(data, mask, iterations=3, output=None):
    '''Substitute pixels in mask by a bilinear least square fitting.
    '''
    out = output if output is not None else data.copy()
    
    # A binary mask, regions are ones
    binry = mask != 0
    
    # Label regions in the binary mask
    lblarr, labl = ndimage.label(binry)
    
    # Structure for dilation is 8-way
    stct = ndimage.generate_binary_structure(2, 2)
    # Pixels in the background
    back = lblarr == 0
    # For each object
    for idx in range(1, labl + 1):
        # Pixels of the object
        segm = lblarr == idx
        # Pixels of the object or the background
        # dilation will only touch these pixels
        dilmask =  numpy.logical_or(back, segm)
        # Dilation 3 times
        more = ndimage.binary_dilation(segm, stct, 
                                       iterations=iterations, 
                                       mask=dilmask)
        # Border pixels
        # Pixels in the border around the object are
        # more and (not segm)
        border = numpy.logical_and(more, numpy.logical_not(segm))
        # Pixels in the border
        xi, yi = border.nonzero()
        # Bilinear leastsq calculator
        calc = FitOne(xi, yi, out[xi,yi])
        # Pixels in the region
        xi, yi = segm.nonzero()
        # Value is obtained from the fit
        out[segm] = calc(xi, yi)
        
    return out

class ImageInformation(object):
    def __init__(self):
        pass


class Recipe(RecipeBase, EmirRecipeMixin):
    '''Recipe for the reduction of imaging mode observations.

    Recipe to reduce observations obtained in imaging mode, considering different
    possibilities depending on the size of the offsets between individual images.
    In particular, the following observing modes are considered: stare imaging, nodded
    beamswitched imaging, and dithered imaging. 
    
    A critical piece of information here is a table that clearly specifies which
    images can be labeled as *science*, and which ones as *sky*. Note that some
    images are used both as *science* and *sky* (when the size of the targets are
    small compared to the offsets).
    
    **Observing modes:**
     
     * StareImage
     * Nodded/Beam-switched images
     * Dithered images 
    
    
    **Inputs:**
    
     * Science frames + [Sky Frames]
     * Observing mode name: **stare image**, **nodded beamswitched image**, or **dithered imaging**
     * A table relating each science image with its sky image(s) (TBD if it's in 
       the FITS header and/or in other format)
     * Offsets between them (Offsets must be integer)
     * Master Dark 
     * Bad pixel mask (BPM) 
     * Non-linearity correction polynomials 
     * Master flat (twilight/dome flats)
     * Master background (thermal background, only in K band)
     * Exposure Time (must be the same in all the frames)
     * Airmass for each frame
     * Detector model (gain, RN, lecture mode)
     * Average extinction in the filter
     * Astrometric calibration (TBD)
    
    **Outputs:**
    
     * Image with three extensions: final image scaled to the individual exposure
       time, variance  and exposure time map OR number of images combined (TBD)
    
    **Procedure:**
    
    Images are corrected from dark, non-linearity and flat. Then, an iterative
    process starts:
    
     * Sky is computed from each frame, using the list of sky images of each
       science frame. The objects are avoided using a mask (from the second
       iteration on).
    
     * The relative offsets are the nominal from the telescope. From the second
       iteration on, we refine them using objects of appropriate brightness (not
       too bright, not to faint).
    
     * We combine the sky-subtracted images, output is: a new image, a variance
       image and a exposure map/number of images used map.
    
     * An object mask is generated.
    
     * We recompute the sky map, using the object mask as an additional input. From
       here we iterate (typically 4 times).
    
     * Finally, the images are corrected from atmospheric extinction and flux
       calibrated.
    
     * A preliminary astrometric calibration can always be used (using the central
       coordinates of the pointing and the plate scale in the detector). A better
       calibration might be computed using available stars (TBD).
    
    '''
    capabilities = ['dithered_images',
                    'nodded-beamswitched_images',
                    'stare_images']
    
    required_parameters = [
        Schema('extinction', ProxyQuery(dummy=1.0), 'Mean atmospheric extinction'),
        Schema('master_bias', ProxyQuery(), 'Master bias image'),
        Schema('master_dark', ProxyQuery(), 'Master dark image'),
        Schema('master_bpm', ProxyQuery(), 'Master bad pixel mask'),
        Schema('master_flat', ProxyQuery(), 'Master flat field image'),
        Schema('nonlinearity', ProxyQuery(dummy=[1.0, 0.0]), 'Polynomial for non-linearity correction'),
        Schema('iterations', 4, 'Iterations of the recipe'),
        Schema('images', ProxyPath('/observing_block/result/images'), 'A list of paths to images'),
        Schema('resultname', 'result.fits', 'Name of the output image'),
        Schema('airmasskey', 'AIRMASS', 'Name of airmass header keyword'),
        Schema('exposurekey', 'EXPOSED', 'Name of exposure header keyword'),
        # Sextractor parameter files
        Schema('sexfile', None, 'Sextractor parameter file'),
        Schema('paramfile', None, 'Sextractor parameter file'),
        Schema('nnwfile', None, 'Sextractor parameter file'),
        Schema('convfile', None, 'Sextractor parameter file'),
    ]
    
    provides = []
    
    def __init__(self, param, runinfo):
        super(Recipe, self).__init__(param, runinfo)

    def setup(self):
        # Parameters will store the image with absolute paths
        self.parameters['master_dark'] = DiskImage(os.path.abspath(self.parameters['master_dark']))
        self.parameters['master_flat'] = DiskImage(os.path.abspath(self.parameters['master_flat']))
        self.parameters['master_bpm'] = DiskImage(os.path.abspath(self.parameters['master_bpm']))
        
        # Converting self.parameters['images'] to DiskImage
        # with absolute path
        r = dict((key, numina.image.DiskImage(filename=os.path.abspath(key))) 
             for key in self.parameters['images'])
        
        for key, val in self.parameters['images'].items():                
            self.parameters['images'][key] = (r[key], val[0], [r[key] for key in val[1]])
        
        d = {}
        for key in ['sexfile', 'paramfile', 'nnwfile', 'convfile']:
            d[key] = os.path.abspath(self.parameters[key])
            
        self.sconf = SextractorConf(**d)
    
    def basic_processing(self, image, bpm, dark):
        hdulist = pyfits.open(image.base, mode='update')
        try:
            hdu = hdulist['primary']                
            _logger.info('Interpolating BPM in %s', image.label)
            if not hdu.header.has_key('NFIXPIX'):
                hdu.data = fixpix(hdu.data, bpm)
                hdu.header.update('NFIXPIX', 'done')

            _logger.info('Correcting dark %s', image.label)
            if not hdu.header.has_key('NMDARK'):
                hdu.data -= dark
                hdu.header.update('NMDARK', 'done')
        finally:
            hdulist.close()

    def compute_simple_sky(self, image, iteration):
        
        dst = _name_skysub_proc(image.label, iteration)
        prev = image.lastname
        shutil.copy(image.lastname, dst)
        image.lastname = dst
        
        hdulist1 = pyfits.open(image.lastname, mode='update')
        try:
            data = hdulist1['primary'].data
            d = data[image.region]
            
            if image.objmask_data is not None:
                m = image.objmask_data[image.region]
                sky = numpy.median(d[m == 0])
            else:
                _logger.debug('object mask empty')
                sky = numpy.median(d)

            _logger.debug('median sky value is %f', sky)
            image.median_sky = sky
            
            _logger.info('Iter %d, SC: subtracting sky to image %s', 
                         iteration, prev)
            region = image.region
            data[region] -= sky
            
        finally:
            hdulist1.close()
            
    def compute_advanced_sky(self, image, objmask, iteration):
        # Create a copy of the image
        dst = _name_skysub_proc(image.label, iteration)
        shutil.copy(image.lastname, dst)
        image.lastname = dst
        
        _logger.info('Computing advanced sky for %s', image.label)
        desc = []
        data = []
        masks = []

        try:        
            for i in itertools.chain(*image.sky_related):
                filename = i.flat_corrected
                hdulist = pyfits.open(filename, mode='update')
                data.append(hdulist['primary'].data[i.region])
                masks.append(objmask[i.region])
                desc.append(hdulist)

            sky, _, num = median(data, masks)
            if numpy.any(num == 0):
                # We have pixels without
                # sky background information
                _logger.warn('Pixels without sky information in image %s',
                             i.flat_corrected)
                binmask = num == 0
                # FIXME: during development, this is faster
                sky[binmask] = sky[num != 0].mean()
                # To continue we interpolate over the patches
                #fixpix2(sky, binmask, output=sky, iterations=1)
                name = _name_skybackgroundmask(image.label, iteration)
                pyfits.writeto(name, binmask.astype('int16'))
                

            hdulist1 = pyfits.open(image.lastname, mode='update')
            try:
                d = hdulist1['primary'].data[image.region]
            
                _logger.info('Iter %d, SC: subtracting sky to image %s', 
                         iteration, i.flat_corrected)
                name = _name_skybackground(image.label, iteration)
                pyfits.writeto(name, sky)
                d -= sky
            
            finally:
                hdulist1.close()
                                                       
        finally:
            for hdl in desc:
                hdl.close()
            
            
                
    def combine_images(self, iinfo, iteration):
        _logger.debug('Iter %d, opening sky-subtracted images', iteration)
        imgslll = [pyfits.open(image.lastname, mode='readonly', memmap=True) for image in iinfo]
        _logger.debug('Iter %d, opening mask images', iteration)
        mskslll = [pyfits.open(image.resized_mask, mode='readonly', memmap=True) for image in iinfo]
        try:
            extinc = [pow(10, 0.4 * image.airmass * self.parameters['extinction']) for image in iinfo]
            data = [i['primary'].data for i in imgslll]
            masks = [i['primary'].data for i in mskslll]
            sf_data = median(data, masks, scales=extinc, dtype='float32')

            # We are saving here only data part
            pyfits.writeto('result_i%0d.fits' % iteration, sf_data[0], clobber=True)
                        
            return sf_data
            
        finally:
            _logger.debug('Iter %d, closing sky-subtracted images', iteration)
            map(lambda x: x.close(), imgslll)
            _logger.debug('Iter %d, closing mask images', iteration)
            map(lambda x: x.close(), mskslll)        

    def compute_superflat(self, iinfo, iteration):
        try:
            filelist = []
            data = []
            masks = []
            for image in iinfo:
                _logger.debug('Iter %d, opening resized image %s', iteration, image.resized_base)
                hdulist = pyfits.open(image.resized_base, memmap=True, mode='readonly')
                filelist.append(hdulist)
                data.append(hdulist['primary'].data[image.region])
                _logger.debug('Iter %d, opening mask image %s', iteration, image.resized_mask)
                hdulist = pyfits.open(image.resized_mask, memmap=True, mode='readonly')
                filelist.append(hdulist)
                masks.append(hdulist['primary'].data[image.region])

                scales = [image.median_scale for image in iinfo]
                
            _logger.debug('Iter %d, combining images', iteration)
            sf_data, _sf_var, _sf_num = flatcombine(data, masks, scales=scales, method='median', blank=1.0 / scales[0])
            
            # _fs_num == 0 where there's no information
            # these values have been scales to 1.0 / scales[0]
            # so is safe to fit something here
            
            _logger.debug('Iter %d, fitting to a smooth surface', iteration)
            pc, fitted = imsurfit(sf_data, order=2, output_fit=True)
            _logger.info('polynomial fit %s', pc)
            #fitted /= fitted.mean()            
            
            sfhdu = pyfits.PrimaryHDU(sf_data[0])            
            sfhdu.writeto(_name_skyflat('comb', iteration))
            sfhdu = pyfits.PrimaryHDU(fitted)            
            sfhdu.writeto(_name_skyflat('fit', iteration))
            return fitted
        finally:
            _logger.debug('Iter %d, closing resized images and mask', iteration)
            for fileh in filelist:               
                fileh.close()        

    def correct_superflat(self, image, fitted, iteration):
        _logger.info("Iter %d, SF: apply superflat to image %s", iteration, image.resized_base)
        hdulist = pyfits.open(image.resized_base, mode='readonly')
        data = hdulist['primary'].data[image.region]
        newdata = hdulist['primary'].data.copy()
        newdata[image.region] = correct_flatfield(data, fitted)
        newheader = hdulist['primary'].header.copy()
        hdulist.close()
        phdu = pyfits.PrimaryHDU(newdata, newheader)
        image.lastname = _name_skyflat_proc(image.label, iteration)
        image.flat_corrected = image.lastname
        phdu.writeto(image.lastname)
        
    def resize_image_and_mask(self, image, finalshape, imgn, maskn):
        _logger.info('Resizing image %s', image.label)
        resize_fits(image.base, imgn, finalshape, image.region)
    
        _logger.info('Resizing mask %s', image.label)
        resize_fits(image.mask, maskn, finalshape, image.region)
            
    def run(self):
        images_info = []
        
        for key in sorted(self.parameters['images'].keys()):
            # Label
            ii = ImageInformation()
            ii.label = os.path.splitext(key)[0]
            ii.base = self.parameters['images'][key][0].filename
            ii.mask = self.parameters['master_bpm'].filename
            ii.offset = self.parameters['images'][key][1]
 
            ii.region = None
            ii.resized_base = None
            ii.resized_mask = None
            ii.objmask = None
            ii.objmask_data = None
            hdr = pyfits.getheader(ii.base)
            try:
                ii.baseshape = get_image_shape(hdr)
                ii.airmass = hdr[self.parameters['airmasskey']]
            except KeyError, e:
                raise RecipeError("%s in image %s" % (str(e), ii.base))
            images_info.append(ii)
    
        images_info = update_sky_related(images_info, nimages=2)
        image_shapes = images_info[0].baseshape
    
        _logger.info('Basic processing')

        # Basic processing
        # Open bpm, dark and flat
        bpm = pyfits.getdata(self.parameters['master_bpm'].filename)
        mdark = pyfits.getdata(self.parameters['master_dark'].filename)
        # FIXME: Use a flat field image eventually
        #mflat = pyfits.getdata(self.parameters['master_flat'].filename)

        for image in images_info:
            self.basic_processing(image, bpm, mdark)
        
        del bpm
        del mdark
        
        niteration = self.parameters['iterations']
        
        # Final image, not yet built
        sf_data = None
        
        for iter_ in range(1, niteration + 1):
            # Resizing images
            _logger.info('Iter %d, computing offsets', iter_)
            offsets = [image.offset for image in images_info]        
            finalshape, offsetsp = combine_shape(image_shapes, offsets)
            _logger.info('Shape of resized array is %s', finalshape)
            
            _logger.info('Iter %d, resizing images and masks', iter_)            
            for image, noffset in zip(images_info, offsetsp):
                region, _ = subarray_match(finalshape, noffset, image.baseshape)
                image.region = region
                image.noffset = noffset
                imgn, maskn = _name_redimensioned_images(image.label, iter_)
                image.resized_base = imgn
                image.resized_mask = maskn
                
                self.resize_image_and_mask(image, finalshape, imgn, maskn)

            _logger.info('Iter %d, generating segmentation image', iter_)            
            if sf_data is not None:
                # sextractor takes care of bad pixels
                objmask = create_object_mask(self.sconf, sf_data[0], _name_segmask(iter_))
            else:
                objmask = numpy.zeros(finalshape, dtype='int')
                                        
            # Update objects mask      
            for image in images_info:
                _logger.info('Iter %d, create object mask %s', iter_, image.objmask) 
                image.objmask = _name_object_mask(image.label, iter_)
                image.objmask_data = objmask[image.region]
                pyfits.writeto(image.objmask, image.objmask_data)
                            
            _logger.info('Iter %d, superflat correction (SF)', iter_)
            _logger.info('Iter %d, SF: computing scale factors', iter_)

            for image in images_info:
                region = image.region
                data = pyfits.getdata(image.resized_base)[region]
                mask = pyfits.getdata(image.resized_mask)[region]
                image.median_scale = numpy.median(data[mask == 0])
                _logger.debug('median value of %s is %f', image.resized_base, image.median_scale)
            
            # Combining images to obtain the sky flat 
            _logger.info("Iter %d, SF: combining the images without offsets", iter_)
            fitted = self.compute_superflat(images_info, iter_)
            
            _logger.info("Iter %d, SF: apply superflat", iter_)
            # Process all images with the fitted flat
            for image in images_info:
                self.correct_superflat(image, fitted, iter_)
            
            _logger.info('Iter %d, sky correction (SC)', iter_)
            # In the first iteration
            if iter_ == 1:
                _logger.info('Iter %d, SC: computing simple sky', iter_)
                for image in images_info:            
                    self.compute_simple_sky(image, iter_)
            else:
                _logger.info('Iter %d, SC: computing adavanced sky', iter_)
                for image in images_info:            
                    self.compute_advanced_sky(image, objmask, iter_)
    
            # Combining the images
            _logger.info("Iter %d, Combining the images", iter_)
            sf_data = self.combine_images(images_info, iter_)
            

          
            _logger.info('Iter %d, finished', iter_)

        primary_headers = {'FILENAME': self.parameters['resultname']}
        result = create_result(sf_data[0], headers=primary_headers,
                                variance=sf_data[1], 
                                exmap=sf_data[2].astype('int16'))
        
        _logger.info("Final image created")
        return {'qa': numina.qa.UNKNOWN, 'result_image': result}
