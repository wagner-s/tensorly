import numpy as np
import warnings

import tensorly as tl
from ..random import check_random_state
from ..base import unfold
from ..kruskal_tensor import kruskal_to_tensor
from ..tenalg import khatri_rao

# Authors: Jean Kossaifi <jean.kossaifi+tensors@gmail.com>
#          Chris Swierczewski <csw@amazon.com>
#          Sam Schneider <samjohnschneider@gmail.com>
#          Aaron Meurer <asmeurer@gmail.com>

# License: BSD 3 clause


def normalize_factors(factors):
    """Normalizes factors to unit length and returns factor magnitudes

    Turns ``factors = [|U_1, ... U_n|]`` into ``[weights; |V_1, ... V_n|]``,
    where the columns of each `V_k` are normalized to unit Euclidean length
    from the columns of `U_k` with the normalizing constants absorbed into
    `weights`. In the special case of a symmetric tensor, `weights` holds the
    eigenvalues of the tensor.

    Parameters
    ----------
    factors : ndarray list
        list of matrices, all with the same number of columns
        i.e.::
            for u in U:
                u[i].shape == (s_i, R)

        where `R` is fixed while `s_i` can vary with `i`

    Returns
    -------
    normalized_factors : list of ndarrays
        list of matrices with the same shape as `factors`
    weights : ndarray
        vector of length `R` holding normalizing constants

    """
    # allocate variables for weights, and normalized factors
    rank = factors[0].shape[1]
    weights = tl.ones(rank, **tl.context(factors[0]))
    normalized_factors = []

    # normalize columns of factor matrices
    for factor in factors:
        scales = tl.norm(factor, axis=0)
        weights *= scales
        scales_non_zero = tl.where(scales==0, tl.ones(tl.shape(scales), **tl.context(factors[0])), scales)
        normalized_factors.append(factor/scales_non_zero)
    return normalized_factors, weights


def initialize_factors(tensor, rank, init='svd', svd='numpy_svd', random_state=None, non_negative=False):
    r"""Initialize factors used in `parafac`.

    The type of initialization is set using `init`. If `init == 'random'` then
    initialize factor matrices using `random_state`. If `init == 'svd'` then
    initialize the `m`th factor matrix using the `rank` left singular vectors
    of the `m`th unfolding of the input tensor.

    Parameters
    ----------
    tensor : ndarray
    rank : int
    init : {'svd', 'random'}, optional
    svd : str, default is 'numpy_svd'
        function to use to compute the SVD, acceptable values in tensorly.SVD_FUNS
    non_negative : bool, default is False
        if True, non-negative factors are returned

    Returns
    -------
    factors : ndarray list
        List of initialized factors of the CP decomposition where element `i`
        is of shape (tensor.shape[i], rank)

    """
    rng = check_random_state(random_state)

    if init == 'random':
        factors = [tl.tensor(rng.random_sample((tensor.shape[i], rank)), **tl.context(tensor)) for i in range(tl.ndim(tensor))]
        if non_negative:
            return [tl.abs(f) for f in factors]
        else:
            return factors
    elif init == 'svd':
        try:
            svd_fun = tl.SVD_FUNS[svd]
        except KeyError:
            message = 'Got svd={}. However, for the current backend ({}), the possible choices are {}'.format(
                    svd, tl.get_backend(), tl.SVD_FUNS)
            raise ValueError(message)

        factors = []
        for mode in range(tl.ndim(tensor)):
            U, _, _ = svd_fun(unfold(tensor, mode), n_eigenvecs=rank)

            if tensor.shape[mode] < rank:
                # TODO: this is a hack but it seems to do the job for now
                # factor = tl.tensor(np.zeros((U.shape[0], rank)), **tl.context(tensor))
                # factor[:, tensor.shape[mode]:] = tl.tensor(rng.random_sample((U.shape[0], rank - tl.shape(tensor)[mode])), **tl.context(tensor))
                # factor[:, :tensor.shape[mode]] = U
                random_part = tl.tensor(rng.random_sample((U.shape[0], rank - tl.shape(tensor)[mode])), **tl.context(tensor))
                U = tl.concatenate([U, random_part], axis=1)
            if non_negative:
                factors.append(tl.abs(U[:, :rank]))
            else:
                factors.append(U[:, :rank])
        return factors

    raise ValueError('Initialization method "{}" not recognized'.format(init))


def parafac(tensor, rank, n_iter_max=100, init='svd', svd='numpy_svd', tol=1e-8,
            orthogonalise=False, random_state=None, verbose=False,
            return_errors=False, non_negative=False):
    """CANDECOMP/PARAFAC decomposition via alternating least squares (ALS)

    Computes a rank-`rank` decomposition of `tensor` [1]_ such that,

        ``tensor = [| factors[0], ..., factors[-1] |]``.

    Parameters
    ----------
    tensor : ndarray
    rank  : int
        Number of components.
    n_iter_max : int
        Maximum number of iteration
    init : {'svd', 'random'}, optional
        Type of factor matrix initialization. See `initialize_factors`.
    svd : str, default is 'numpy_svd'
        function to use to compute the SVD, acceptable values in tensorly.SVD_FUNS
    tol : float, optional
        (Default: 1e-6) Relative reconstruction error tolerance. The
        algorithm is considered to have found the global minimum when the
        reconstruction error is less than `tol`.
    random_state : {None, int, np.random.RandomState}
    verbose : int, optional
        Level of verbosity
    return_errors : bool, optional
        Activate return of iteration errors
    non_negative : bool, optional
        Perform non_negative PARAFAC. See :func:`non_negative_parafac`.

    Returns
    -------
    factors : ndarray list
        List of factors of the CP decomposition element `i` is of shape
        (tensor.shape[i], rank)
    errors : list
        A list of reconstruction errors at each iteration of the algorithms.

    References
    ----------
    .. [1] tl.G.Kolda and B.W.Bader, "Tensor Decompositions and Applications",
       SIAM REVIEW, vol. 51, n. 3, pp. 455-500, 2009.
    """
    epsilon = 10e-12

    if orthogonalise and not isinstance(orthogonalise, int):
        orthogonalise = n_iter_max

    factors = initialize_factors(tensor, rank, init=init, svd=svd,
                                 random_state=random_state,
                                 non_negative=non_negative)
    rec_errors = []
    norm_tensor = tl.norm(tensor, 2)

    for iteration in range(n_iter_max):
        if orthogonalise and iteration <= orthogonalise:
            factor = [tl.qr(factor)[0] for factor in factors]

        if verbose:
            print("Starting iteration", iteration)
        for mode in range(tl.ndim(tensor)):
            if verbose:
                print("Mode", mode, "of", tl.ndim(tensor))
            if non_negative:
                accum = 1
                # khatri_rao(factors).tl.dot(khatri_rao(factors))
                # simplifies to multiplications
                sub_indices = [i for i in range(len(factors)) if i != mode]
                for i, e in enumerate(sub_indices):
                    if i:
                        accum *= tl.dot(tl.transpose(factors[e]), factors[e])
                    else:
                        accum = tl.dot(tl.transpose(factors[e]), factors[e])

            pseudo_inverse = tl.tensor(np.ones((rank, rank)), **tl.context(tensor))
            for i, factor in enumerate(factors):
                if i != mode:
                    pseudo_inverse = pseudo_inverse*tl.dot(tl.transpose(factor), factor)

            mttkrp = tl.tenalg.unfolding_dot_khatri_rao(tensor, factors, mode)

            if non_negative:
                numerator = tl.clip(mttkrp, a_min=epsilon, a_max=None)
                denominator = tl.dot(factors[mode], accum)
                denominator = tl.clip(denominator, a_min=epsilon, a_max=None)
                factor = factors[mode] * numerator / denominator
            else:
                factor = tl.transpose(tl.solve(tl.transpose(pseudo_inverse), tl.transpose(mttkrp)))

            factors[mode] = factor

        if tol:
            # ||tensor - rec||^2 = ||tensor||^2 + ||rec||^2 - 2*<tensor, rec>
            # This is ||kruskal_to_tensor(factors)||^2
            factors_norm = tl.sum(tl.prod(tl.stack([tl.dot(tl.transpose(f), f) for f in factors], 0), 0))
            # mttkrp and factor for the last mode. This is equivalent to the
            # inner product <tensor, factorization>
            iprod = tl.sum(mttkrp*factor)
            rec_error = tl.sqrt(tl.abs(norm_tensor**2 + factors_norm - 2*iprod)) / norm_tensor
            rec_errors.append(rec_error)

            if iteration >= 1:
                if verbose:
                    print('reconstruction error={}, variation={}.'.format(
                        rec_errors[-1], rec_errors[-2] - rec_errors[-1]))

                if tol and abs(rec_errors[-2] - rec_errors[-1]) < tol:
                    if verbose:
                        print('converged in {} iterations.'.format(iteration))
                    break
            else:
                if verbose:
                    print('reconstruction error={}'.format(rec_errors[-1]))

    if return_errors:
        return factors, rec_errors
    else:
        return factors


def non_negative_parafac(tensor, rank, n_iter_max=100, init='svd', svd='numpy_svd',
                         tol=10e-7, random_state=None, verbose=0):
    """
    Non-negative CP decomposition

    Uses multiplicative updates, see [2]_

    This is the same as parafac(non_negative=True).

    Parameters
    ----------
    tensor : ndarray
    rank   : int
            number of components
    n_iter_max : int
                 maximum number of iteration
    init : {'svd', 'random'}, optional
    svd : str, default is 'numpy_svd'
        function to use to compute the SVD, acceptable values in tensorly.SVD_FUNS
    tol : float, optional
          tolerance: the algorithm stops when the variation in
          the reconstruction error is less than the tolerance
    random_state : {None, int, np.random.RandomState}
    verbose : int, optional
        level of verbosity

    Returns
    -------
    factors : ndarray list
            list of positive factors of the CP decomposition
            element `i` is of shape ``(tensor.shape[i], rank)``

    References
    ----------
    .. [2] Amnon Shashua and Tamir Hazan,
       "Non-negative tensor factorization with applications to statistics and computer vision",
       In Proceedings of the International Conference on Machine Learning (ICML),
       pp 792-799, ICML, 2005
    """
    return parafac(tensor, rank, n_iter_max=n_iter_max, init=init, svd=svd,
                   tol=tol, random_state=random_state, verbose=verbose, non_negative=True)


def sample_khatri_rao(matrices, n_samples, skip_matrix=None,
                      return_sampled_rows=False, random_state=None):
    """Random subsample of the Khatri-Rao product of the given list of matrices

        If one matrix only is given, that matrix is directly returned.

    Parameters
    ----------
    matrices : ndarray list
        list of matrices with the same number of columns, i.e.::

            for i in len(matrices):
                matrices[i].shape = (n_i, m)

    n_samples : int
        number of samples to be taken from the Khatri-Rao product

    skip_matrix : None or int, optional, default is None
        if not None, index of a matrix to skip

    random_state : None, int or numpy.random.RandomState
        if int, used to set the seed of the random number generator
        if numpy.random.RandomState, used to generate random_samples

    returned_sampled_rows : bool, default is False
        if True, also returns a list of the rows sampled from the full
        khatri-rao product

    Returns
    -------
    sampled_Khatri_Rao : ndarray
        The sampled matricised tensor Khatri-Rao with `n_samples` rows

    indices : tuple list
        a list of indices sampled for each mode

    indices_kr : int list
        list of length `n_samples` containing the sampled row indices
    """
    if random_state is None or not isinstance(random_state, np.random.RandomState):
        rng = check_random_state(random_state)
        warnings.warn('You are creating a new random number generator at each call.\n'
                      'If you are calling sample_khatri_rao inside a loop this will be slow:'
                      ' best to create a rng outside and pass it as argument (random_state=rng).')
    else:
        rng = random_state

    if skip_matrix is not None:
        matrices = [matrices[i] for i in range(len(matrices)) if i != skip_matrix]

    rank = tl.shape(matrices[0])[1]
    sizes = [tl.shape(m)[0] for m in matrices]

    # For each matrix, randomly choose n_samples indices for which to compute the khatri-rao product
    indices_list = [rng.randint(0, tl.shape(m)[0], size=n_samples, dtype=int) for m in matrices]
    if return_sampled_rows:
        # Compute corresponding rows of the full khatri-rao product
        indices_kr = np.zeros((n_samples), dtype=int)
        for size, indices in zip(sizes, indices_list):
            indices_kr = indices_kr*size + indices

    # Compute the Khatri-Rao product for the chosen indices
    sampled_kr = tl.ones((n_samples, rank), **tl.context(matrices[0]))
    for indices, matrix in zip(indices_list, matrices):
        sampled_kr = sampled_kr*matrix[indices, :]

    if return_sampled_rows:
        return sampled_kr, indices_list, indices_kr
    else:
        return sampled_kr, indices_list


def randomised_parafac(tensor, rank, n_samples, n_iter_max=100, init='random', svd='numpy_svd',
                       tol=10e-9, max_stagnation=20, random_state=None, verbose=1):
    """Randomised CP decomposition via sampled ALS

    Parameters
    ----------
    tensor : ndarray
    rank   : int
            number of components
    n_samples : int
                number of samples per ALS step
    n_iter_max : int
                 maximum number of iteration
    init : {'svd', 'random'}, optional
    svd : str, default is 'numpy_svd'
        function to use to compute the SVD, acceptable values in tensorly.SVD_FUNS
    tol : float, optional
          tolerance: the algorithm stops when the variation in
          the reconstruction error is less than the tolerance
    max_stagnation: int, optional, default is 0
                    if not zero, the maximum allowed number
                    of iterations with no decrease in fit
    random_state : {None, int, np.random.RandomState}, default is None
    verbose : int, optional
        level of verbosity

    Returns
    -------
    factors : ndarray list
            list of positive factors of the CP decomposition
            element `i` is of shape ``(tensor.shape[i], rank)``

    References
    ----------
    .. [3] Casey Battaglino, Grey Ballard and Tamara G. Kolda,
       "A Practical Randomized CP Tensor Decomposition",
    """
    rng = check_random_state(random_state)
    factors = initialize_factors(tensor, rank, init=init, svd=svd, random_state=random_state)
    rec_errors = []
    n_dims = tl.ndim(tensor)
    norm_tensor = tl.norm(tensor, 2)
    min_error = 0

    for iteration in range(n_iter_max):
        for mode in range(n_dims):
            kr_prod, indices_list = sample_khatri_rao(factors, n_samples, skip_matrix=mode, random_state=rng)
            indices_list = [i.tolist() for i in indices_list]
            # Keep all the elements of the currently considered mode
            indices_list.insert(mode, slice(None, None, None))
            # MXNet will not be happy if this is a list insteaf of a tuple
            indices_list = tuple(indices_list)
            if mode:
                sampled_unfolding = tensor[indices_list]
            else:
                sampled_unfolding = tl.transpose(tensor[indices_list])

            pseudo_inverse = tl.dot(tl.transpose(kr_prod), kr_prod)
            factor = tl.dot(tl.transpose(kr_prod), sampled_unfolding)
            factor = tl.transpose(tl.solve(pseudo_inverse, factor))
            factors[mode] = factor

        if max_stagnation or tol:
            rec_error = tl.norm(tensor - kruskal_to_tensor(factors), 2) / norm_tensor
            if not min_error or rec_error < min_error:
                min_error = rec_error
                stagnation = -1
            stagnation += 1

            rec_errors.append(rec_error)

            if iteration > 1:
                if verbose:
                    print('reconstruction error={}, variation={}.'.format(
                        rec_errors[-1], rec_errors[-2] - rec_errors[-1]))

                if (tol and abs(rec_errors[-2] - rec_errors[-1]) < tol) or \
                   (stagnation and (stagnation > max_stagnation)):
                    if verbose:
                        print('converged in {} iterations.'.format(iteration))
                    break

    return factors

def _parafac2(X, r=2, tol=1e-5, verbose=True):
    from functools import reduce
    m = len(X)
    F = np.identity(r)
    D = np.ones((m, r))
    A = np.linalg.eigh(reduce(lambda A, B: A + B, map(lambda Xi: Xi.T.dot(Xi), X)))
    A = A[1][:, np.argsort(A[0])][:, -r:]

    H = [np.linalg.qr(Xi, mode='r') if Xi.shape[0] > Xi.shape[1] else Xi for Xi in X]
    G = [np.identity(r), np.identity(r), np.ones((r, r)) * m]

    err = 1
    conv = False
    niters = 0
    while not conv and niters < 100:
        P = [np.linalg.svd((F * D[i, :]).dot(H[i].dot(A).T), full_matrices=0) for i in range(m)]
        P = [(S[0].dot(S[2])).T for S in P]
        T = np.array([P[i].T.dot(H[i]) for i in range(m)])

        F = np.reshape(np.transpose(T, (0, 2, 1)), (-1, T.shape[1])).T.dot( _KhatriRao(D, A)).dot(np.linalg.pinv(G[2] * G[1]))
        G[0] = F.T.dot(F)
        A = np.reshape(np.transpose(T, (0, 1, 2)), (-1, T.shape[2])).T.dot( _KhatriRao(D, F)).dot(np.linalg.pinv(G[2] * G[0]))
        G[1] = A.T.dot(A)
        D = np.reshape(np.transpose(T, (2, 1, 0)), (-1, T.shape[0])).T.dot( _KhatriRao(A, F)).dot(np.linalg.pinv(G[1] * G[0]))
        G[2] = D.T.dot(D)
        err_old = err
        err = np.sum(np.sum((H[i] - (P[i].dot(F) * D[i, :]).dot(A.T)) ** 2) for i in range(m))
        niters += 1
        conv = abs(err_old - err) < tol * err_old
        if verbose: print("Iteration {0}; error = {1:.6f}".format(niters, err))

    P = [np.linalg.svd((F * D[i, :]).dot(X[i].dot(A).T), full_matrices=0) for i in range(m)]
    F = [(S[0].dot(S[2])).T.dot(F) for S in P]
    return F, D, A

def _KhatriRao(A, B):
    return np.repeat(A, B.shape[0], axis=0) * np.tile(B, (A.shape[0], 1))

def parafac2(X, r=2, tol=1e-5, verbose=True, svd='numpy_svd'):
    from functools import reduce

    try:
        svd_fun = tl.SVD_FUNS[svd]
    except KeyError:
        message = 'Got svd={}. However, for the current backend ({}), the' \
                   ' possible choices are {}'.format(svd,                  \
                    tl.get_backend(), tl.SVD_FUNS)
        raise ValueError(message)
    m = len(X)
    # XXX: not available in tl
    F = np.identity(r)
    # np -> tl
    # np: numpy.ones(shape, dtype=None, order='C')
    # tl: ones(shape, dtype=None)
    D = tl.ones((m, r))
    # XXX: not available in tl, core backend uses eigsh from scipy
    A = np.linalg.eigh(reduce(lambda A, B: A + B, map(lambda Xi: Xi.T.dot(Xi), X)))
    # XXX: not available in tl
    A = A[1][:, np.argsort(A[0])][:, -r:]
    # np -> tl
    # np:  numpy.linalg.qr(a, mode='reduced')
    # tl:  qr(a)
    H = [np.linalg.qr(Xi, mode='r') if Xi.shape[0] > Xi.shape[1] else Xi for Xi in X]
    # XXX: identity not available in tl, ones -> tl
    G = [np.identity(r), np.identity(r), tl.ones((r, r)) * m]

    err = 1
    conv = False
    niters = 0
    while not conv and niters < 100:
        # np -> tl
        # np:  ndarray.dot(b, out=None)
        # tl:  dot(a, b)
        # np: np.linalg.svd
        # tl: tl.SVD_FUN['numpy_svd'] -> partial_svd? XXX: can this be mapped w/ parameter for n_eigenvecs?
        P = [np.linalg.svd(tl.dot(F * D[i, :], tl.dot(H[i], A).T), full_matrices=False) for i in range(m)]
        # np -> tl: dot
        P = [tl.dot(S[0], S[2]).T for S in P]
        # np -> tl: dot
        T = np.array([tl.dot(P[i].T, H[i]) for i in range(m)])

        # np -> tl:
        # np:  numpy.reshape(a, newshape, order='C')
        # tl:  reshape(tensor, newshape)
        # XXX: transpose not completely supported
        # np:  numpy.transpose(a, axes=None)
        # tl: transpose(tensor)
        # XXX: pinv
        # np:  numpy.linalg.pinv(a, rcond=1e-15)
        F = tl.reshape(
            np.transpose(T, (0, 2, 1)),
            (-1, T.shape[1])).T.dot( _KhatriRao(D, A)).dot(np.linalg.pinv(G[2] * G[1]))
        # np -> tl: dot
        G[0] = tl.dot(F.T, F)
        # np -> tl: reshape
        A = tl.reshape(np.transpose(T, (0, 1, 2)), (-1, T.shape[2])).T.dot( _KhatriRao(D, F)).dot(np.linalg.pinv(G[2] * G[0]))
        # np -> tl: dot
        G[1] = tl.dot(A.T, A)
        # np -> tl: reshape
        D = tl.reshape(np.transpose(T, (2, 1, 0)), (-1, T.shape[0])).T.dot( _KhatriRao(A, F)).dot(np.linalg.pinv(G[1] * G[0]))
        # np -> tl: dot
        G[2] = tl.dot(D.T, D)
        err_old = err
        # np -> tl: dot
        # np -> tl
        # np:  numpy.sum(a, axis=None, dtype=None, out=None, keepdims=<no value>, initial=<no value>)
        # tl:  sum(tensor, axis=None)
        err = tl.sum(tl.sum((H[i] - tl.dot(tl.dot(P[i], F) * D[i, :], A.T)) ** 2) for i in range(m))
        niters += 1
        conv = abs(err_old - err) < tol * err_old
        if verbose: print("Iteration {0}; error = {1:.6f}".format(niters, err))

    # XXX: which SVD?
    # np -> tl: dot, svd
    P = [np.linalg.svd(tl.dot(F * D[i, :], tl.dot(X[i], A).T), full_matrices=False) for i in range(m)]
    # np -> tl: dot
    F = [tl.dot((tl.dot(S[0], S[2])).T, F) for S in P]
    return F, D, A
