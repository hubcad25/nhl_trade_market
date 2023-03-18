# Notebook of learning ML

## 3Blue1Brown

### First video
[Link]https://www.youtube.com/watch?v=aircAruvnKk&t=1s
Example of the video: using nn to predict what number is written on the screen
Long short-term memory network --> good for speech recognition

**Neuron**: thing that hold a number between 0 and 1. Number = activation. It's more of a function
As we advance in the hidden layers, the subcomponents of each number become clearer. For example, the last layer contains the exact shapes of the numbers. The output is predicted by knowing which shapes are activated in this last layer.

**Weights**: if a neuron of the first layer is activated if a horizontal line in the top of the image is there, all pixels that form this line will have positive weights. We add the weighted sum of the grey scale of each pixel in the new output (the sum can be any real number) and pass it through a sigmoid function (like for glm regressions?) to put it between 0 and 1. We can also introduce a **bias** *inside* the sigmoid function to change the "threshold" of activity. It's simply an addition or a substraction of a number.

The sigmoid function is slow to learn and infers activity even though the neuron activation is below 0. The newer method is to use the ReLU (rectified linear unit) function.

**Learning**: letting the system find the right **weights** and **biases**

