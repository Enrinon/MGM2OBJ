<h1 align="center">MGM2OBJ</h1>
<h3 align="center">MGM Model File Converter</h3>
<p align="center">A Python-based 3D geometry extraction tool.</p>
<h1 align="left">About The Project</h1>

Extract 3D geometry from .mgm files and export it to .obj.

A reverse-engineering project focused on understanding and extracting 3D geometry from MGM model files.

<p align="Center">More specifically Mami Tomoe TPS </p>

<h1 align="left">Features:</h1>

•MGM geometry extraction

•Wavefront OBJ export

•Place Textures (I'm currently working on it)

<h1 align="left">Known Issues</h1>

I'll say this, it's a pretty early version, so you might encounter some bugs.

**•Texture failure**

Textures are not placed in the generated file

<h1 align="left">How To Use</h1>

You can download the files in releases

To use it correctly, you need Python 3 and a terminal, then run this code:

`python MGM_converter.py stage#.mgm`

This will calculate the entire model of the .mgm file.

But if you have the original textures requested by the file, run this code instead (It's not working yet):

`python MGM_converter.py stage#.mgm --textures TestFolder`

All of this was done with AI assistance (But I verified it and I made sure it worked well enough)
