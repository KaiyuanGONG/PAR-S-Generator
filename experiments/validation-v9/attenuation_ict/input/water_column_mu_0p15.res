


              SIMIND Monte Carlo Simulation Program    V8.0  
------------------------------------------------------------------------------
 Phantom S : h2o       Crystal...: czt       InputFile.: attenuation_ict   
 Phantom B : h2o       BackScatt.: pmt       OutputFile: water_column_mu_0p
 Collimator: pb_sb2    SourceRout: smap      SourceImg.: water_column_mu_0p
 Cover.....: al        ScoreRout.: penetrate DensityImg: water_column_mu_0p
------------------------------------------------------------------------------
 PhotonEnergy.......: 140          ge-legp   PhotonsPerProj....: 10000          
 EnergyResolution...: 6.3          SPECT     Activity..........: 1704           
 MaxScatterOrder....: 3            BScatt    DetectorLenght....: 25.585         
 DetectorWidth......: 19.68        Cover     DetectorHeight....: 0.725          
 UpperEneWindowTresh: 154          Phantom   Distance to det...: 30             
 LowerEneWindowTresh: 126          Resolut   ShiftSource X.....: 0              
 PixelSize  I.......: 0.442        SaveMap   ShiftSource Y.....: 0              
 PixelSize  J.......: 0.442                  ShiftSource Z.....: 0              
 HalfLength S.......: 28.285                 HalfLength P......: 28.285         
 HalfWidth  S.......: 28.285                 HalfWidth  P......: 28.285         
 HalfHeight S.......: 28.285                 HalfHeight P......: 28.285         
 SourceType.........: XcatBinMap             PhantomType.......: XcatBinMap   
------------------------------------------------------------------------------
 GENERAL DATA
 keV/channel........: 0.5                    CutoffEnergy......: 0              
 Photons/Bq.........: 0.879                  StartingAngle.....: 180            
 CameraOffset X.....: 0                      CoverThickness....: 0.1            
 CameraOffset Y.....: 0                      BackscatterThickn.: 0.1            
 MatrixSize I.......: 128                    IntrinsicResolut..: 0              
 MatrixSize J.......: 128                    AcceptanceAngle...: 2.87511        
 Emission type......: 2                      Initial Weight....: 149781.6       
 NN ScalingFactor...: 10000                  Energy Channels...: 512            
                                                                              
 SOLID STATE DETECTOR SETTINGS 
 MobilLife electrons: 5                      MobilLife holes...: 0.4            
 Voltage anod/cathod: 600                    Contact pad size..: 0.16           
 Number detectors  I: 128                    Number Detectors J: 128            
 Anode element pitch: 0.246                  Tau decayConstant.: 0.4            
 EnergyResolut model: -2                     Hetch Model.......: 1              
 Flat detector shift: 1                      CloudMobility.....: 0.225          
                                                                              
 SPECT DATA
 RotationMode.......: 360                    Nr of Projections.: 60             
 RotationAngle......: 6                      Projection.[start]: 1              
 Orbital fraction...: 1                      Projection...[end]: 60             
                                                                              
 COLLIMATOR DATA FOR ROUTINE: MC RayTracing       
 CollimatorCode.....: ge-legp                CollimatorType....: Parallel 
 HoleSize X.........: 0.226                  Distance X........: 0.02           
 HoleSize Y.........: 0.226                  Distance Y........: 0.02           
 CenterShift X......: 0                      X-Ray flag........: F              
 CenterShift Y......: 0                      CollimThickness...: 4.5            
 HoleShape..........: Rectangular            Space Coll2Det....: 0              
 CollDepValue [57]..: 0                      CollDepValue [58].: 0              
 CollDepValue [59]..: 0                      CollDepValue [60].: 0              

 PHOTONS AFTER COLLIMATOR AND WITHIN ENER-WIN
 Geometric..........:  97.72 %          97.01 %
 Penetration........:   1.10 %           1.97 %
 Scatter in collim..:   1.18 %           1.02 %
 X-rays in collim...:   0.00 %           0.00 %
                                                                              
 IMAGE-BASED PHANTOM DATA
 RotationCentre.....:  65, 65                Bone definition...: 1170           
 CT-Pixel size......: 0.442                  Slice thickness...: 0.44195        
 StartImage.........: 1                      No of CT-Images...: 128            
 MatrixSize I.......: 128                    CTmapOrientation..: 0              
 MatrixSize J.......: 128                    StepSize..........: 0.1            
 CenterPoint I......: 65                     ShiftPhantom X....: 0              
 CenterPoint J......: 65                     ShiftPhantom Y....: 0              
 CenterPoint K......: 65                     ShiftPhantom Z....: 0              
                                                                              
 INFO FOR TCT file
 MatrixSize I.......: 128                    MatrixSize J......: 128            
 MatrixSize K.......: 128                    Units.............: mu                  
 Scout File.........: F
  
------------------------------------------------------------------------------
PENETRATE ROUTINE - B=BackScatter, P=PhantomScatter

                       Energy Window %      :      Total Spectrum %
                 -B,-P  -B,+P  +B,-P   +B+P  -B,-P  -B,+P  +B,-P   +B+P
 geometric....:  77.76  19.25   0.00   0.00  34.40  63.30   0.02   0.00
 penetration..:   1.58   0.39   0.00   0.00   0.47   0.63   0.00   0.00
 collim scatt.:   0.83   0.19   0.00   0.00   0.36   0.82   0.00   0.00
 collim x-rays:   0.00   0.00   0.00   0.00   0.00   0.00   0.00   0.00
                                                                              
 INTERACTIONS IN THE CRYSTAL
 MaxValue spectrum..: 0.2505E+06     
 MaxValue projection: 4230.          
 CountRate spectrum.: 0.6841E+06     
 CountRate E-Window.: 0.9902E+05     
                                                                              
 SCATTER IN ENERGY WINDOW
 Scatter/Primary....: 0.24742        
 Scatter/Total......: 0.19835        
 Scatter order 1....: 89.21 %        
 Scatter order 2....: 9.86 %         
 Scatter order 3....: 0.93 %         
                                                                              
 CALCULATED DETECTOR PARAMETERS
 Efficiency E-window: 0.1404         
 Efficiency spectrum: 0.9699         
 Sensitivity Cps/MBq: 58.1107        
 Sensitivity Cpm/uCi: 129.0057       
                                                                              
 Simulation started.: 2026:08:18 09:56:54
 Simulation stopped.: 2026:08:18 09:57:05
 Elapsed time.......: 0 h, 0 m and 11 s
 DetectorHits.......: 11693          
 DetectorHits/CPUsec: 1104           
                                                                              
 OTHER INFORMATION
 GE NM/CT 870 CZT simulation config paired type-7 mu-times-voxel analyt
 Compiled 2025:01:28 with INTEL Win   
 Current random number generator: ranmar
 Energy resolution as function of 1/sqrt(E)
 Linear angle sampling within acceptance angle
 Inifile: simind.ini
 Command: attenuation_ict water_column_mu_0p15 /FS:water_column_mu_0p15 /FD:water_column_mu_0p15 /NN:10000 /IN:x22,3x/84:4
