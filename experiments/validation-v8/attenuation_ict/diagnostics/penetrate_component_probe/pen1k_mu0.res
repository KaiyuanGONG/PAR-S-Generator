


              SIMIND Monte Carlo Simulation Program    V8.0  
------------------------------------------------------------------------------
 Phantom S : h2o       Crystal...: czt       InputFile.: attenuation_ict   
 Phantom B : h2o       BackScatt.: pmt       OutputFile: pen1k_mu0         
 Collimator: pb_sb2    SourceRout: smap      SourceImg.: water_column_mu_0p
 Cover.....: al        ScoreRout.: penetrate DensityImg: water_column_mu_0p
------------------------------------------------------------------------------
 PhotonEnergy.......: 140          ge-legp   PhotonsPerProj....: 1000           
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
 SourceType.........: Integer2Map            PhantomType.......: Integer2Map  
------------------------------------------------------------------------------
 GENERAL DATA
 keV/channel........: 0.5                    CutoffEnergy......: 0              
 Photons/Bq.........: 0.879                  StartingAngle.....: 180            
 CameraOffset X.....: 0                      CoverThickness....: 0.1            
 CameraOffset Y.....: 0                      BackscatterThickn.: 0.1            
 MatrixSize I.......: 128                    IntrinsicResolut..: 0              
 MatrixSize J.......: 128                    AcceptanceAngle...: 2.87511        
 Emission type......: 2                      Initial Weight....: 1497816        
 NN ScalingFactor...: 1000                   Energy Channels...: 512            
                                                                              
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
 Geometric..........:  97.55 %          96.94 %
 Penetration........:   1.50 %           1.97 %
 Scatter in collim..:   0.94 %           1.09 %
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
 MatrixSize K.......: 128                    Units.............: g/cm3*1000          
 Scout File.........: F
  
------------------------------------------------------------------------------
PENETRATE ROUTINE - B=BackScatter, P=PhantomScatter

                       Energy Window %      :      Total Spectrum %
                 -B,-P  -B,+P  +B,-P   +B+P  -B,-P  -B,+P  +B,-P   +B+P
 geometric....:  96.94   0.00   0.00   0.00  97.50   0.00   0.06   0.00
 penetration..:   1.97   0.00   0.00   0.00   1.51   0.00   0.00   0.00
 collim scatt.:   1.09   0.00   0.00   0.00   0.93   0.00   0.00   0.00
 collim x-rays:   0.00   0.00   0.00   0.00   0.00   0.00   0.00   0.00
                                                                              
 INTERACTIONS IN THE CRYSTAL
 MaxValue spectrum..: 0.1904E+06     
 MaxValue projection: 6944.          
 CountRate spectrum.: 0.2408E+06     
 CountRate E-Window.: 0.7974E+05     
                                                                              
 CALCULATED DETECTOR PARAMETERS
 Efficiency E-window: 0.3159         
 Efficiency spectrum: 0.9539         
 Sensitivity Cps/MBq: 46.7987        
 Sensitivity Cpm/uCi: 103.8931       
                                                                              
 Simulation started.: 2026:08:18 01:21:37
 Simulation stopped.: 2026:08:18 01:21:42
 Elapsed time.......: 0 h, 0 m and 5 s
 DetectorHits.......: 424            
 DetectorHits/CPUsec: 83             
                                                                              
 OTHER INFORMATION
 GE NM/CT 870 CZT simulation config paired type-1 source/density analyt
 Compiled 2025:01:28 with INTEL Win   
 Current random number generator: ranmar
 Energy resolution as function of 1/sqrt(E)
 Linear angle sampling within acceptance angle
 Inifile: simind.ini
 Command: attenuation_ict pen1k_mu0 /FS:water_column_mu_0p00 /FD:water_column_mu_0p00 /NN:1000 /PX:0.442/84:4
