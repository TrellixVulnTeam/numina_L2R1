#
# Copyright 2015-2017 Universidad Complutense de Madrid
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

"""Utility functions to handle image distortions."""

from __future__ import division
from __future__ import print_function

import numpy as np
from skimage import transform

from numina.array.display.pause_debugplot import pause_debugplot
from numina.array.display.ximplotxy import ximplotxy


def compute_distortion(x_orig, y_orig, x_rect, y_rect, order, debugplot):
    """Compute image distortion transformation.

    This function computes the following 2D transformation:
    x_orig = sum[i=0:order]( sum[j=0:j]( a_ij * x_rect**(i - j) * y_rect**j ))
    y_orig = sum[i=0:order]( sum[j=0:j]( b_ij * x_rect**(i - j) * y_rect**j ))

    Parameters
    ----------
    x_orig : numpy array
        X coordinate of the reference points in the distorted image
    y_orig : numpy array
        Y coordinate of the reference points in the distorted image
    x_rect : numpy array
        X coordinate of the reference points in the rectified image
    y_rect : numpy array
        Y coordinate of the reference points in the rectified image
    order : int
        Order of the polynomial transformation
    debugplot : int
        Determine whether intermediate computations and/or plots
        are displayed. The valid codes are defined in
        numina.array.display.pause_debugplot.

    Returns
    -------

    aij : numpy array
        Coefficients a_ij of the 2D transformation.
    bij : numpy array
        Coefficients b_ij of the 2D transformation.

    """

    # protections
    npoints = len(x_orig)
    for xdum in [y_orig, x_rect, y_rect]:
        if len(xdum) != npoints:
            raise ValueError('Unexpected different number of points')

    # normalize ranges dividing by the maximum, so that the transformation
    # fit will be computed with data points with coordinates in the range [0,1]
    x_scale = 1.0 / np.concatenate((x_orig,
                                    x_rect)).max()
    y_scale = 1.0 / np.concatenate((y_orig,
                                    y_rect)).max()
    x_orig_scaled = x_orig * x_scale
    y_orig_scaled = y_orig * y_scale
    x_inter_scaled = x_rect * x_scale
    y_inter_scaled = y_rect * y_scale

    # solve 2 systems of equations with half number of unknowns each
    if order == 1:
        a_matrix = np.vstack([np.ones(npoints),
                              x_inter_scaled,
                              y_inter_scaled]).T
    elif order == 2:
        a_matrix = np.vstack([np.ones(npoints),
                              x_inter_scaled,
                              y_inter_scaled,
                              x_inter_scaled ** 2,
                              x_inter_scaled * y_orig_scaled,
                              y_inter_scaled ** 2]).T
    elif order == 3:
        a_matrix = np.vstack([np.ones(npoints),
                              x_inter_scaled,
                              y_inter_scaled,
                              x_inter_scaled ** 2,
                              x_inter_scaled * y_orig_scaled,
                              y_inter_scaled ** 2,
                              x_inter_scaled ** 3,
                              x_inter_scaled ** 2 * y_inter_scaled,
                              x_inter_scaled * y_inter_scaled ** 2,
                              y_inter_scaled ** 3]).T
    elif order == 4:
        a_matrix = np.vstack([np.ones(npoints),
                              x_inter_scaled,
                              y_inter_scaled,
                              x_inter_scaled ** 2,
                              x_inter_scaled * y_orig_scaled,
                              y_inter_scaled ** 2,
                              x_inter_scaled ** 3,
                              x_inter_scaled ** 2 * y_inter_scaled,
                              x_inter_scaled * y_inter_scaled ** 2,
                              y_inter_scaled ** 3,
                              x_inter_scaled ** 4,
                              x_inter_scaled ** 3 * y_inter_scaled ** 1,
                              x_inter_scaled ** 2 * y_inter_scaled ** 2,
                              x_inter_scaled ** 1 * y_inter_scaled ** 3,
                              y_inter_scaled ** 4]).T
    else:
        raise ValueError("Invalid order=" + str(order))
    poltrans = transform.PolynomialTransform(
        np.vstack(
            [np.linalg.lstsq(a_matrix, x_orig_scaled)[0],
             np.linalg.lstsq(a_matrix, y_orig_scaled)[0]]
        )
    )

    # reverse normalization to recover coefficients of the
    # transformation in the correct system
    factor = np.zeros_like(poltrans.params[0])
    k = 0
    for i in range(order + 1):
        for j in range(i + 1):
            factor[k] = (x_scale ** (i - j)) * (y_scale ** j)
            k += 1
    aij = poltrans.params[0] * factor / x_scale
    bij = poltrans.params[1] * factor / y_scale

    # show results
    if abs(debugplot) >= 10:
        print(">>> u=u(x,y) --> aij:\n", aij)
        print(">>> v=v(x,y) --> bij:\n", bij)

    if abs(debugplot) % 10 != 0:
        ax = ximplotxy(x_orig_scaled, y_orig_scaled,
                       show=False,
                       **{'marker': 'o', # 'color': 'cyan',
                          'label': '(u,v) coordinates', 'linestyle': ''})
        dum = zip(x_orig_scaled, y_orig_scaled)
        for idum in range(len(dum)):
            ax.text(dum[idum][0], dum[idum][1], str(idum + 1), fontsize=10,
                    horizontalalignment='center',
                    verticalalignment='bottom', color='black')
        ax.plot(x_inter_scaled, y_inter_scaled, 'o',
                label="(x,y) coordinates")
        dum = zip(x_inter_scaled, y_inter_scaled)
        for idum in range(len(dum)):
            ax.text(dum[idum][0], dum[idum][1], str(idum + 1), fontsize=10,
                    horizontalalignment='center',
                    verticalalignment='bottom', color='grey')
        xmin = np.concatenate((x_orig_scaled,
                               x_inter_scaled)).min()
        xmax = np.concatenate((x_orig_scaled,
                               x_inter_scaled)).max()
        ymin = np.concatenate((y_orig_scaled,
                               y_inter_scaled)).min()
        ymax = np.concatenate((y_orig_scaled,
                               y_inter_scaled)).max()
        dx = xmax - xmin
        xmin -= dx / 20
        xmax += dx / 20
        dy = ymax - ymin
        ymin -= dy / 20
        ymax += dy / 20
        ax.set_xlim([xmin, xmax])
        ax.set_ylim([ymin, ymax])
        ax.set_xlabel("pixel (normalized coordinate)")
        ax.set_ylabel("pixel (normalized coordinate)")
        ax.set_title("compute distortion")
        ax.legend()
        pause_debugplot(debugplot, pltshow=True)

    return aij, bij


def fmap(order, aij, bij, x, y):
    """Evaluate the 2D polynomial transformation.

    u = sum[i=0:order]( sum[j=0:j]( a_ij * x**(i - j) * y**j ))
    v = sum[i=0:order]( sum[j=0:j]( b_ij * x**(i - j) * y**j ))

    Parameters
    ----------
    order : int
        Order of the polynomial transformation.
    aij : numpy array
        Polynomial coefficents corresponding to a_ij.
    bij : numpy array
        Polynomial coefficents corresponding to b_ij.
    x : numpy array or float
        X coordinate values where the transformation is computed. Note
        that these values correspond to array indices.
    y : numpy array or float
        Y coordinate values where the transformation is computed. Note
        that these values correspond to array indices.

    Returns
    -------
    u : numpy array or float
        U coordinate values.
    v : numpy array or float
        V coordinate values.

    """

    u = np.zeros_like(x)
    v = np.zeros_like(y)

    k = 0
    for i in range(order + 1):
        for j in range(i + 1):
            u += aij[k] * (x ** (i - j)) * (y ** j)
            v += bij[k] * (x ** (i - j)) * (y ** j)
            k += 1

    return u, v


def ncoef_fmap(order):
    """Expected number of coefficients in a 2D transformation of a given order.

    Parameters
    ----------
    order : int
        Order of the 2D polynomial transformation.

    Returns
    -------
    ncoef : int
        Expected number of coefficients.

    """

    ncoef = 0
    for i in range(order + 1):
        for j in range(i + 1):
            ncoef += 1
    return ncoef


def rectify2d(image2d, aij, bij, resampling,
              naxis1out=None, naxis2out=None,
              ioffx=None, ioffy=None,
              debugplot=0):
    """Rectify image applying the provided 2D transformation.

    The rectified image correspond to the transformation given by:
        u = sum[i=0:order]( sum[j=0:j]( a_ij * x**(i - j) * y**j ))
        v = sum[i=0:order]( sum[j=0:j]( b_ij * x**(i - j) * y**j ))

    Parameters
    ----------
    image2d : 2d numpy array
        Initial image.
    aij : 1d numpy array
        Coefficients a_ij of the transformation.
    bij : 1d numpy array
        Coefficients b_ij of the transformation.
    resampling : int
        1: nearest neighbour, 2: flux preserving interpolation.
    naxis1out : int or None
        X-axis dimension of output image.
    naxis2out : int or None
        Y-axis dimension of output image.
    ioffx : int
        Integer offset in the X direction.
    ioffy : int
        Integer offset in the Y direction.
    debugplot : int
        Determines whether intermediate computations and/or plots
        are displayed. The valid codes are defined in
        numina.array.display.pause_debugplot

    Returns
    -------
    image2d_rect : 2d numpy array
        Rectified image.

    """

    # protections
    ncoef = len(aij)
    if len(bij) != ncoef:
        raise ValueError("aij and bij lengths are different!")

    # order of the polynomial transformation
    loop = True
    order = 0
    while loop:
        loop = not (ncoef == ncoef_fmap(order))
        if loop:
            order += 1
            if order > 4:
                raise ValueError("order > 4 not implemented")
    if abs(debugplot) >= 10:
        print('--> rectification order:', order)

    # initial image dimension
    naxis2, naxis1 = image2d.shape

    # output image dimension
    if naxis1out is None:
        naxis1out = naxis1
    if naxis2out is None:
        naxis2out = naxis2
    if ioffx is None:
        ioffx = 0
    if ioffy is None:
        ioffy = 0

    # initialize result
    image2d_rect = np.zeros((naxis2out, naxis1out))

    if resampling == 1:
        # pixel coordinates (rectified image); since the fmap function
        # below requires floats, these arrays must use dtype=np.float
        j = np.arange(0, naxis1out, dtype=np.float) - ioffx
        i = np.arange(0, naxis2out, dtype=np.float) - ioffy
        # the cartesian product of the previous 1D arrays could be stored
        # as np.transpose([xx,yy]), where xx and yy are computed as follows
        xx = np.tile(j, (len(i),))
        yy = np.repeat(i, len(j))
        # compute pixel coordinates in original (distorted) image
        xxx, yyy = fmap(order, aij, bij, xx, yy)
        # round to nearest integer and cast to integer; note that the
        # rounding still provides a float, so the casting is required
        ixxx = np.rint(xxx).astype(np.int)
        iyyy = np.rint(yyy).astype(np.int)
        # determine pixel coordinates within available image
        lxxx = np.logical_and(ixxx >= 0, ixxx < naxis1)
        lyyy = np.logical_and(iyyy >= 0, iyyy < naxis2)
        lok = np.logical_and(lxxx, lyyy)
        # assign pixel values to rectified image
        ixx = xx.astype(np.int)[lok]
        iyy = yy.astype(np.int)[lok]
        ixxx = ixxx[lok]
        iyyy = iyyy[lok]
        image2d_rect[iyy + ioffy, ixx + ioffx] = image2d[iyyy, ixxx]
    else:
        raise ValueError("Sorry, this resampling method has not been"
                         " implemented yet!")

    # return result
    return image2d_rect
