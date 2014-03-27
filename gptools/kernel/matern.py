# Copyright 2013 Mark Chilenski
# This program is distributed under the terms of the GNU General Purpose License (GPL).
# Refer to http://www.gnu.org/licenses/gpl.txt
# 
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# 
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
# 
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

"""Provides the :py:class:`MaternKernel` class which implements the anisotropic Matern kernel.
"""

from __future__ import division

from .core import ChainRuleKernel
from ..utils import generate_set_partitions

import scipy
import scipy.special
import warnings
try:
    import mpmath
except ImportError:
    warnings.warn("Could not import mpmath. Certain functions of the Matern kernel will not function.",
                  ImportWarning)

class MaternKernel(ChainRuleKernel):
    r"""Matern covariance kernel. Supports arbitrary derivatives. Treats order as a hyperparameter.
    
    The Matern kernel has the following hyperparameters, always referenced in
    the order listed:
    
    = ===== ====================================
    0 sigma prefactor
    1 nu    order of kernel
    2 l1    length scale for the first dimension
    3 l2    ...and so on for all dimensions
    = ===== ====================================
    
    The kernel is defined as:
    
    .. math::
    
        k_M = \sigma^2 \frac{2^{1-\nu}}{\Gamma(\nu)}
        \left (\sqrt{2\nu \sum_i\left (\frac{\tau_i^2}{l_i^2}\right )}\right )^\nu
        K_\nu\left(\sqrt{2\nu \sum_i\left(\frac{\tau_i^2}{l_i^2}\right)}\right)

    Parameters
    ----------
    num_dim : int
        Number of dimensions of the input data. Must be consistent with the `X`
        and `Xstar` values passed to the :py:class:`~gptools.gaussian_process.GaussianProcess`
        you wish to use the covariance kernel with.
    **kwargs
        All keyword parameters are passed to :py:class:`~gptools.kernel.core.ChainRuleKernel`.

    Raises
    ------
    ValueError
        If `num_dim` is not a positive integer or the lengths of the input
        vectors are inconsistent.
    GPArgumentError
        If `fixed_params` is passed but `initial_params` is not.
    """
    def __init__(self, num_dim, **kwargs):
        super(MaternKernel, self).__init__(num_dim,
                                           num_dim + 2,
                                           **kwargs)
    
    def _compute_k(self, tau):
        r"""Evaluate the kernel directly at the given values of `tau`.
        
        Parameters
        ----------
        tau : :py:class:`Matrix`, (`M`, `N`)
            `M` inputs with dimension `N`.
        
        Returns
        -------
        k : :py:class:`Array`, (`M`,)
            :math:`k(\tau)` (less the :math:`\sigma^2` prefactor).
        """
        y, r2l2 = self._compute_y(tau, return_r2l2=True)
        k = 2.0**(1 - self.nu) / scipy.special.gamma(self.nu) * y**self.nu * scipy.special.kv(self.nu, y)
        k[r2l2 == 0] = 1
        return k
    
    def _compute_y(self, tau, return_r2l2=False):
        r"""Covert tau to :math:`y=\sqrt{2\nu\sum_i(\tau_i^2/l_i^2)}`.
        
        Parameters
        ----------
        tau : :py:class:`Matrix`, (`M`, `N`)
            `M` inputs with dimension `N`.
        return_r2l2 : bool, optional
            Set to True to return a tuple of (`y`, `r2l2`). Default is False
            (only return `y`).
        
        Returns
        -------
        y : :py:class:`Array`, (`M`,)
            Inner argument of function.
        r2l2 : :py:class:`Array`, (`M`,)
            Anisotropically scaled distances. Only returned if `return_r2l2` is True.
        """
        r2l2 = self._compute_r2l2(tau)
        y = scipy.sqrt(2.0 * self.nu * r2l2)
        if return_r2l2:
            return (y, r2l2)
        else:
            return y
    
    def _compute_y_wrapper(self, *args):
        r"""Convert tau to :math:`y=\sqrt{2\nu\sum_i(\tau_i^2/l_i^2)}`.
        
        Takes `tau` as an argument list for compatibility with :py:func:`mpmath.diff`.
        
        Parameters
        ----------
        tau[0] : scalar float
            First element of `tau`.
        tau[1] : And so on...
        
        Returns
        -------
        y : scalar float
            Inner part of Matern kernel at the given `tau`.
        """
        return self._compute_y(scipy.atleast_2d(scipy.asarray(args, dtype=float)))
    
    def _compute_dk_dy(self, y, n):
        r"""Evaluate the derivative of the outer form of the Matern kernel.
        
        Uses the general Leibniz rule to compute the n-th derivative of:
        
        .. math::
        
            f(y) = \frac{2^{1-\nu}}{\Gamma(\nu)} y^\nu K_\nu(y)
        
        Notice that this is very poorly-behaved at :math:`x=0`. There, the
        value is approximated using :py:func:`mpmath.diff` with the `singular`
        keyword. This is rather slow, so if you require a fixed value of `nu`
        you may wish to consider implementing the appropriate kernel separately.
        
        Parameters
        ----------
        y : :py:class:`Array`, (`M`,)
            `M` inputs to evaluate at.
        n : non-negative scalar int.
            Order of derivative to compute.
        
        Returns
        -------
        dk_dy : :py:class:`Array`, (`M`,)
            Specified derivative at specified locations.
        """
        
        dk_dy = scipy.zeros_like(y, dtype=float)
        non_zero_idxs = (y != 0)
        for k in xrange(0, n + 1):
            dk_dy[non_zero_idxs] += (scipy.special.binom(n, k) *
                                     scipy.special.poch(1 - k + self.nu, k) *
                                     (y[non_zero_idxs])**(-k + self.nu) *
                                     scipy.special.kvp(self.nu, y[non_zero_idxs], n=n-k))
        
        # Handle the cases near y=0.
        # Compute the appropriate value using mpmath's arbitrary precision
        # arithmetic. This is potentially slow, but seems to behave pretty
        # well. In cases where the value should be infinite, very large
        # (but still finite) floats are returned with the appropriate sign.
        
        # TODO: These can probably be stored as they are computed if it
        # ends up being too slow.
        if n >= 2 * self.nu:
            warnings.warn("n >= 2*nu can yield inaccurate results.", RuntimeWarning)
        
        # Use John Wright's expression for n < 2 * nu:
        if n < 2.0 * self.nu:
            if n % 2 == 1:
                dk_dy[~non_zero_idxs] = 0.0
            else:
                m = n / 2.0
                dk_dy[~non_zero_idxs] = (
                    (-1.0)**m *
                    2.0**(self.nu - 1.0 - n) *
                    scipy.special.gamma(self.nu - m) *
                    scipy.misc.factorial(n) / scipy.misc.factorial(m)
                )
        else:
            # Fall back to mpmath to handle n >= 2 * nu:
            core_expr = lambda x: x**self.nu * mpmath.besselk(self.nu, x)
            deriv = mpmath.chop(mpmath.diff(core_expr, 0, n=n, singular=True, direction=1))
            dk_dy[~non_zero_idxs] = deriv
        
        dk_dy *= 2.0**(1 - self.nu) / (scipy.special.gamma(self.nu))
        
        return dk_dy  
    
    def _compute_dy_dtau(self, tau, b, r2l2):
        r"""Evaluate the derivative of the inner argument of the Matern kernel.
        
        Uses Faa di Bruno's formula to take the derivative of
        
        .. math::
        
            y = \sqrt{2 \nu \sum_i(\tau_i^2 / l_i^2)}
        
        Parameters
        ----------
        tau : :py:class:`Matrix`, (`M`, `N`)
            `M` inputs with dimension `N`.
        b : :py:class:`Array`, (`P`,)
            Block specifying derivatives to be evaluated.
        r2l2 : :py:class:`Array`, (`M`,)
            Precomputed anisotropically scaled distance.
        
        Returns
        -------
        dy_dtau: :py:class:`Array`, (`M`,)
            Specified derivative at specified locations.
        """
        deriv_partitions = generate_set_partitions(b)
        dy_dtau = scipy.zeros_like(r2l2, dtype=float)
        non_zero_idxs = (r2l2 != 0)
        for p in deriv_partitions:
            dy_dtau[non_zero_idxs] += self._compute_dy_dtau_on_partition(tau[non_zero_idxs], p, r2l2[non_zero_idxs])
        
        # Case at tau=0 is handled with mpmath for now.
        # TODO: This is painfully slow! Figure out how to do this analytically!
        derivs = scipy.zeros(tau.shape[1], dtype=int)
        for d in b:
            derivs[d] += 1
        dy_dtau[~non_zero_idxs] = mpmath.chop(
            mpmath.diff(
                self._compute_y_wrapper,
                scipy.zeros(tau.shape[1], dtype=float),
                n=derivs,
                singular=True,
                direction=1
            )
        )
        return dy_dtau
    
    def _compute_dy_dtau_on_partition(self, tau, p, r2l2):
        """Evaluate the term inside the sum of Faa di Bruno's formula for the given partition.
        
        Parameters
        ----------
        tau : :py:class:`Matrix`, (`M`, `N`)
            `M` inputs with dimension `N`.
        p : list of :py:class:`Array`
            Each element is a block of the partition representing the derivative
            orders to use.    
        r2l2 : :py:class:`Array`, (`M`,)
            Precomputed anisotropically scaled distance.
        
        Returns
        -------
        dy_dtau : :py:class:`Array`, (`M`,)
            The specified derivatives over the given partition at the specified
            locations.
        """
        n = len(p)
        dy_dtau = scipy.zeros_like(r2l2)
        dy_dtau = (scipy.sqrt(2.0 * self.nu) *
                   scipy.special.poch(1 - n + 0.5, n) *
                   (r2l2)**(-n + 0.5))
        for b in p:
            dy_dtau *= self._compute_dT_dtau(tau, b)
        
        return dy_dtau
    
    def _compute_dT_dtau(self, tau, b):
        r"""Evaluate the derivative of the :math:`\tau^2` sum term.
        
        Parameters
        ----------
            tau : :py:class:`Matrix`, (`M`, `N`)
                `M` inputs with dimension `N`.
            b : :py:class:`Array`, (`P`,)
                Block specifying derivatives to be evaluated.
        
        Returns
        -------
        dT_dtau : :py:class:`Array`, (`M`,)
            Specified derivative at specified locations.
        """
        unique_d = scipy.unique(b)
        # Derivatives of order 3 and up are zero, mixed derivatives are zero.
        if len(b) >= 3 or len(unique_d) > 1:
            return scipy.zeros(tau.shape[0])
        else:
            tau_idx = unique_d[0]
            if len(b) == 1:
                return 2.0 * tau[:, tau_idx] / (self.params[2 + tau_idx])**2.0
            else:
                # len(b) == 2 is the only other possibility here because of
                # the first test.
                return 2.0 / (self.params[2 + tau_idx])**2.0 * scipy.ones(tau.shape[0])
    
    @property
    def nu(self):
        r"""Returns the value of the order :math:`\nu`.
        """
        return self.params[1]
