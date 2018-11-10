"""
This module provides an way for ``discretize`` meshes to be
converted to VTK data objects (and back when possible).
"""
import os
import numpy as np

# from ..utils import cyl2cart

import vtk
import vtk.util.numpy_support as nps
from vtk import VTK_VERSION
from vtk import vtkXMLRectilinearGridWriter
from vtk import vtkXMLUnstructuredGridWriter
from vtk import vtkXMLStructuredGridWriter
from vtk import vtkXMLRectilinearGridReader



def assignCellData(vtkDS, models=None):
    """Assign the model(s) to the VTK dataset as CellData

    Input:
    :param models, dictionary of numpy.array - Name('s) and array('s). Match number of cells
    """
    nc = vtkDS.GetNumberOfCells()
    if models is not None:
        for name, mod in models.items():
            # Convert numpy array
            if mod.size != nc:
                raise RuntimeError('Number of model cells ({}) does not match number of mesh cells ({}).'.format(mod.size, nc))
            vtkDoubleArr = nps.numpy_to_vtk(mod, deep=1)
            vtkDoubleArr.SetName(name)
            vtkDS.GetCellData().AddArray(vtkDoubleArr)
    return vtkDS



class vtkInterface(object):
    """This class is full of methods that enable ``discretize`` meshes to
    be converted to VTK data objects (and back when possible).
    """
    # NOTE: I name mangle the class specific VTK conversions to force the user
    #       to use the ``toVTK()`` method.

    def __treeMeshToVTK(mesh, models=None):
        """
        Constructs a ``vtkUnstructuredGrid`` object of this tree mesh and the
        given models as ``CellData`` of that VTK dataset.

        Input:
        :param mesh, discretize.TreeMesh - The tree mesh to convert to a ``vtkUnstructuredGrid``
        :param models, dictionary of numpy.array - Name('s) and array('s). Match number of cells

        """
        # Make the data parts for the vtu object
        # Points
        ptsMat = np.vstack((mesh.gridN, mesh.gridhN))

        # Adjust if result was 2D (voxels are pixels in 2D):
        VTK_CELL_TYPE = vtk.VTK_VOXEL
        if ptsMat.shape[1] == 2:
            # Add Z values of 0.0 if 2D
            ptsMat = np.c_[ptsMat, np.zeros(ptsMat.shape[0])]
            VTK_CELL_TYPE = vtk.VTK_PIXEL
        if ptsMat.shape[1] != 3:
            raise RuntimeError('Points of the mesh are improperly defined.')
        # Rotate the points to the cartesian system
        ptsMat = np.dot(ptsMat, mesh.rotation_matrix)
        # Grab the points
        vtkPts = vtk.vtkPoints()
        vtkPts.SetData(nps.numpy_to_vtk(ptsMat, deep=True))
        # Cells
        cellArray = [c for c in mesh]
        cellConn = np.array([cell.nodes for cell in cellArray])
        cellsMat = np.concatenate((np.ones((cellConn.shape[0], 1), dtype=int)*cellConn.shape[1], cellConn), axis=1).ravel()
        cellsArr = vtk.vtkCellArray()
        cellsArr.SetNumberOfCells(cellConn.shape[0])
        cellsArr.SetCells(cellConn.shape[0], nps.numpy_to_vtkIdTypeArray(cellsMat, deep=True))
        # Make the object
        output = vtk.vtkUnstructuredGrid()
        output.SetPoints(vtkPts)
        output.SetCells(VTK_CELL_TYPE, cellsArr)
        # Add the level of refinement as a cell array
        cell_levels = np.array([cell._level for cell in cellArray])
        refineLevelArr = nps.numpy_to_vtk(cell_levels, deep=1)
        refineLevelArr.SetName('octreeLevel')
        output.GetCellData().AddArray(refineLevelArr)
        # Assign the model('s) to the object
        return assignCellData(output, models=models)

    @staticmethod
    def __createStructGrid(ptsMat, dims, models=None):
        """An internal helper to build out structured grids"""
        # Adjust if result was 2D:
        if ptsMat.shape[1] == 2:
            # Figure out which dim is null
            nullDim = dims.index(None)
            ptsMat = np.insert(ptsMat, nullDim, np.zeros(ptsMat.shape[0]), axis=1)
        if ptsMat.shape[1] != 3:
            raise RuntimeError('Points of the mesh are improperly defined.')
        # Convert the points
        vtkPts = vtk.vtkPoints()
        vtkPts.SetData(nps.numpy_to_vtk(ptsMat, deep=True))
        # Uncover hidden dimension
        for i, d in enumerate(dims):
            if d is None:
                dims[i] = 0
            dims[i] = dims[i] + 1
        output = vtk.vtkStructuredGrid()
        output.SetDimensions(dims[0], dims[1], dims[2]) # note this subtracts 1
        output.SetPoints(vtkPts)
        # Assign the model('s) to the object
        return assignCellData(output, models=models)

    def __getRotatedNodes(mesh):
        """A helper to get the nodes of a mesh rotated by specified axes"""
        nodes = mesh.gridN
        if mesh.dim == 1:
            nodes = np.c_[mesh.gridN, np.zeros((mesh.nN, 2))]
        elif mesh.dim == 2:
            nodes = np.c_[mesh.gridN, np.zeros((mesh.nN, 1))]
        # Now garuntee nodes are correct
        if nodes.shape != (mesh.nN, 3):
            raise RuntimeError('Nodes of the grid are improperly defined.')
        # Rotate the points based on the axis orientations
        mesh._validate_orientation()
        return np.dot(nodes, mesh.rotation_matrix)

    def __tensorMeshToVTK(mesh, models=None):
        """
        Constructs a ``vtkRectilinearGrid`` (or a ``vtkStructuredGrid``) object
        of this tensor mesh and the given models as ``CellData`` of that grid.
        If the mesh is defined on a normal cartesian system then a rectilinear
        grid is generated. Otherwise, a structured grid is generated.

        Input:
        :param mesh, discretize.TensorMesh - The tensor mesh to convert to a ``vtkRectilinearGrid``
        :param models, dictionary of numpy.array - Name('s) and array('s). Match number of cells

        """
        # Deal with dimensionalities
        if mesh.dim >= 1:
            vX = mesh.vectorNx
            xD = mesh.nNx
            yD, zD = 1, 1
            vY, vZ = np.array([0, 0])
        if mesh.dim >= 2:
            vY = mesh.vectorNy
            yD = mesh.nNy
        if mesh.dim == 3:
            vZ = mesh.vectorNz
            zD = mesh.nNz
        # If axis orientations are standard then use a vtkRectilinearGrid
        if not mesh.reference_is_rotated:
            # Use rectilinear VTK grid.
            # Assign the spatial information.
            output = vtk.vtkRectilinearGrid()
            output.SetDimensions(xD, yD, zD)
            output.SetXCoordinates(nps.numpy_to_vtk(vX, deep=1))
            output.SetYCoordinates(nps.numpy_to_vtk(vY, deep=1))
            output.SetZCoordinates(nps.numpy_to_vtk(vZ, deep=1))
            return assignCellData(output, models=models)
        # Use a structured grid where points are rotated to the cartesian system
        ptsMat = vtkInterface.__getRotatedNodes(mesh)
        dims = [mesh.nCx, mesh.nCy, mesh.nCz]
        # Assign the model('s) to the object
        return vtkInterface.__createStructGrid(ptsMat, dims, models=models)


    def __curvilinearMeshToVTK(mesh, models=None):
        """
        Constructs a ``vtkStructuredGrid`` of this mesh and the given
        models as ``CellData`` of that object.

        Input:
        :param mesh, discretize.CurvilinearMesh - The curvilinear mesh to convert to a ``vtkStructuredGrid``
        :param models, dictionary of numpy.array - Name('s) and array('s). Match number of cells

        """
        ptsMat = vtkInterface.__getRotatedNodes(mesh)
        dims = [mesh.nCx, mesh.nCy, mesh.nCz]
        return vtkInterface.__createStructGrid(ptsMat, dims, models=models)


    def __cylMeshToVTK(mesh, models=None):
        """This treats the CylindricalMesh defined in cylindrical coordinates
        :math:`(r, \theta, z)` and transforms it to cartesian coordinates.
        """
        # # Points
        # ptsMat = cyl2cart(mesh.gridN)
        # dims = [mesh.nCx, mesh.nCy, mesh.nCz]
        # return vtkInterface.__createStructGrid(ptsMat, dims, models=models)
        raise NotImplementedError('`CylMesh`s are not currently supported for VTK conversion.')


    def toVTK(mesh, models=None):
        """Convert any mesh object to it's proper VTK data object."""
        # TODO: mesh.validate()
        converters = {
            'TreeMesh' : vtkInterface.__treeMeshToVTK,
            'TensorMesh' : vtkInterface.__tensorMeshToVTK,
            'CurvilinearMesh' : vtkInterface.__curvilinearMeshToVTK,
            #TODO: 'CylMesh' : vtkInterface.__cylMeshToVTK,
            }
        key = type(mesh).__name__
        try:
            convert = converters[key]
        except:
            raise RuntimeError('Mesh type `%s` is not currently supported for VTK conversion.' % key)
        return convert(mesh, models=models)

    @staticmethod
    def _saveUnstructuredGrid(fileName, vtkUnstructGrid, directory=''):
        """Saves a VTK unstructured grid file (vtu) for an already generated
        ``vtkUnstructuredGrid`` object.

        Input:
        :param str fileName:  path to the output vtk file or just its name if directory is specified
        :param str directory: directory where the UBC GIF file lives
        """
        if not isinstance(vtkUnstructGrid, vtk.vtkUnstructuredGrid):
            raise RuntimeError('`_saveUnstructuredGrid` can only handle `vtkUnstructuredGrid` objects. `%s` is not supported.' % vtkUnstructGrid.__class__)
        # Check the extension of the fileName
        fname = os.path.join(directory, fileName)
        ext = os.path.splitext(fname)[1]
        if ext is '':
            fname = fname + '.vtu'
        elif ext not in '.vtu':
            raise IOError('{:s} is an incorrect extension, has to be .vtu'.format(ext))
        # Make the writer
        vtuWriteFilter = vtkXMLUnstructuredGridWriter()
        if float(VTK_VERSION.split('.')[0]) >= 6:
            vtuWriteFilter.SetInputDataObject(vtkUnstructGrid)
        else:
            vtuWriteFilter.SetInput(vtkUnstructGrid)
        vtuWriteFilter.SetFileName(fileName)
        # Write the file
        vtuWriteFilter.Update()

    @staticmethod
    def _saveStructuredGrid(fileName, vtkStructGrid, directory=''):
        """Saves a VTK structured grid file (vtk) for an already generated
        ``vtkStructuredGrid`` object.

        Input:
        :param str fileName:  path to the output vtk file or just its name if directory is specified
        :param str directory: directory where the UBC GIF file lives
        """
        if not isinstance(vtkStructGrid, vtk.vtkStructuredGrid):
            raise RuntimeError('`_saveStructuredGrid` can only handle `vtkStructuredGrid` objects. `%s` is not supported.' % vtkStructGrid.__class__)
        # Check the extension of the fileName
        fname = os.path.join(directory, fileName)
        ext = os.path.splitext(fname)[1]
        if ext is '':
            fname = fname + '.vts'
        elif ext not in '.vts':
            raise IOError('{:s} is an incorrect extension, has to be .vts'.format(ext))
        # Make the writer
        writer = vtkXMLStructuredGridWriter()
        if float(VTK_VERSION.split('.')[0]) >= 6:
            writer.SetInputDataObject(vtkStructGrid)
        else:
            writer.SetInput(vtkStructGrid)
        writer.SetFileName(fileName)
        # Write the file
        writer.Update()

    @staticmethod
    def _saveRectilinearGrid(fileName, vtkRectGrid, directory=''):
        """Saves a VTK rectilinear file (vtr) ffor an already generated
        ``vtkRectilinearGrid`` object.

        Input:
        :param str fileName:  path to the output vtk file or just its name if directory is specified
        :param str directory: directory where the UBC GIF file lives
        """
        if not isinstance(vtkRectGrid, vtk.vtkRectilinearGrid):
            raise RuntimeError('`_saveRectilinearGrid` can only handle `vtkRectilinearGrid` objects. `%s` is not supported.' % vtkRectGrid.__class__)
        # Check the extension of the fileName
        fname = os.path.join(directory, fileName)
        ext = os.path.splitext(fname)[1]
        if ext is '':
            fname = fname + '.vtr'
        elif ext not in '.vtr':
            raise IOError('{:s} is an incorrect extension, has to be .vtr'.format(ext))
        # Write the file.
        vtrWriteFilter = vtkXMLRectilinearGridWriter()
        if float(VTK_VERSION.split('.')[0]) >= 6:
            vtrWriteFilter.SetInputDataObject(vtkRectGrid)
        else:
            vtuWriteFilter.SetInput(vtuObj)
        vtrWriteFilter.SetFileName(fname)
        vtrWriteFilter.Update()

    def writeVTK(mesh, fileName, models=None, directory=''):
        """Makes and saves a VTK object from this mesh and models

        Input:
        :param str fileName:  path to the output vtk file or just its name if directory is specified
        :param str directory: directory where the UBC GIF file lives
        :param dict models: dictionary of numpy.array - Name('s) and array('s).
        Match number of cells
        """
        vtkObj = vtkInterface.toVTK(mesh, models=models)
        writers = {
            'vtkUnstructuredGrid' : vtkInterface._saveUnstructuredGrid,
            'vtkRectilinearGrid' : vtkInterface._saveRectilinearGrid,
            'vtkStructuredGrid' : vtkInterface._saveStructuredGrid,
            }
        key = type(vtkObj).__name__
        try:
            write = writers[key]
        except:
            raise RuntimeError('VTK data type `%s` is not currently supported.' % key)
        return write(fileName, vtkObj, directory=directory)


class vtkTensorRead(object):
    """Provides a convienance method for reading VTK Rectilinear Grid files
    as TensorMesh objects."""

    @classmethod
    def readVTK(TensorMesh, fileName, directory=''):
        """Read VTK Rectilinear (vtr xml file) and return Tensor mesh and model

        Input:
        :param str fileName: path to the vtr model file to read or just its name if directory is specified
        :param str directory: directory where the UBC GIF file lives

        Output:
        :rtype: tuple
        :return: (TensorMesh, modelDictionary)
        """
        fname = os.path.join(directory, fileName)
        # Read the file
        vtrReader = vtkXMLRectilinearGridReader()
        vtrReader.SetFileName(fname)
        vtrReader.Update()
        vtrGrid = vtrReader.GetOutput()
        # Sort information
        hx = np.abs(np.diff(nps.vtk_to_numpy(vtrGrid.GetXCoordinates())))
        xR = nps.vtk_to_numpy(vtrGrid.GetXCoordinates())[0]
        hy = np.abs(np.diff(nps.vtk_to_numpy(vtrGrid.GetYCoordinates())))
        yR = nps.vtk_to_numpy(vtrGrid.GetYCoordinates())[0]
        zD = np.diff(nps.vtk_to_numpy(vtrGrid.GetZCoordinates()))
        # Check the direction of hz
        if np.all(zD < 0):
            hz = np.abs(zD[::-1])
            zR = nps.vtk_to_numpy(vtrGrid.GetZCoordinates())[-1]
        else:
            hz = np.abs(zD)
            zR = nps.vtk_to_numpy(vtrGrid.GetZCoordinates())[0]
        x0 = np.array([xR, yR, zR])

        # Make the object
        tensMsh = TensorMesh([hx, hy, hz], x0=x0)

        # Grap the models
        models = {}
        for i in np.arange(vtrGrid.GetCellData().GetNumberOfArrays()):
            modelName = vtrGrid.GetCellData().GetArrayName(i)
            if np.all(zD < 0):
                modFlip = nps.vtk_to_numpy(vtrGrid.GetCellData().GetArray(i))
                tM = tensMsh.r(modFlip, 'CC', 'CC', 'M')
                modArr = tensMsh.r(tM[:, :, ::-1], 'CC', 'CC', 'V')
            else:
                modArr = nps.vtk_to_numpy(vtrGrid.GetCellData().GetArray(i))
            models[modelName] = modArr

        # Return the data
        return tensMsh, models
